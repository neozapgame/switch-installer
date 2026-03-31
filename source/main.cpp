#include <switch.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <dirent.h>
#include <sys/stat.h>

#include "usb.h"

// ─── Config ──────────────────────────────────────────────────────────────────
#define QUEUE_FILE      "sdmc:/switch/switch-installer/queue.txt"
#define GAMES_DIR       "sdmc:/switch/switch-installer/"
#define MAX_RETRY       3
#define APP_VERSION     "1.0.0"

// ─── Warna terminal ──────────────────────────────────────────────────────────
#define COLOR_WHITE     "\033[37m"
#define COLOR_GREEN     "\033[32m"
#define COLOR_RED       "\033[31m"
#define COLOR_YELLOW    "\033[33m"
#define COLOR_CYAN      "\033[36m"
#define COLOR_RESET     "\033[0m"
#define COLOR_BOLD      "\033[1m"

// ─── State ───────────────────────────────────────────────────────────────────
struct GameEntry {
    std::string filename;
    std::string filepath;
    bool        done;
    int         retryCount;
};

static std::vector<GameEntry> s_queue;
static int    s_totalGames    = 0;
static int    s_doneGames     = 0;
static int    s_failedGames   = 0;
static bool   s_running       = true;

// ─── Console helpers ──────────────────────────────────────────────────────────

static void clearScreen() {
    printf("\033[2J\033[H");
}

static void printHeader() {
    printf(COLOR_BOLD COLOR_CYAN);
    printf("╔══════════════════════════════════════════╗\n");
    printf("║     SWITCH AUTO INSTALLER v%s        ║\n", APP_VERSION);
    printf("╚══════════════════════════════════════════╝\n");
    printf(COLOR_RESET "\n");
}

static void printStatus(const char* msg, const char* color = COLOR_WHITE) {
    printf("%s%s%s\n", color, msg, COLOR_RESET);
}

static void printProgress(int done, int total, const std::string& current) {
    clearScreen();
    printHeader();

    float pct = total > 0 ? (float)done / total * 100.0f : 0.0f;

    printf(COLOR_BOLD "Progress: " COLOR_RESET
           COLOR_GREEN "%d" COLOR_RESET "/" COLOR_WHITE "%d" COLOR_RESET
           " (%.1f%%)\n\n", done, total, pct);

    // Progress bar
    int barWidth = 40;
    int filled   = total > 0 ? (int)((float)done / total * barWidth) : 0;
    printf("[");
    for (int i = 0; i < barWidth; i++) {
        if (i < filled)  printf(COLOR_GREEN "█" COLOR_RESET);
        else             printf(COLOR_WHITE "░" COLOR_RESET);
    }
    printf("]\n\n");

    if (!current.empty()) {
        printf(COLOR_YELLOW "Installing: " COLOR_RESET);
        // Potong nama kalau terlalu panjang
        if (current.size() > 45) {
            printf("%.42s...\n", current.c_str());
        } else {
            printf("%s\n", current.c_str());
        }
    }

    printf("\n" COLOR_WHITE "Failed: " COLOR_RESET COLOR_RED "%d" COLOR_RESET "\n", s_failedGames);
    printf("\n" COLOR_WHITE "(+) Exit\n" COLOR_RESET);

    consoleUpdate(nullptr);
}

// ─── Queue management ─────────────────────────────────────────────────────────

static bool loadQueue() {
    s_queue.clear();

    FILE* f = fopen(QUEUE_FILE, "r");
    if (!f) {
        printStatus("Queue file tidak ditemukan!", COLOR_RED);
        printStatus("Buat file: sdmc:/switch/switch-installer/queue.txt", COLOR_YELLOW);
        printStatus("Isi dengan nama file game, satu per baris.", COLOR_WHITE);
        consoleUpdate(nullptr);
        return false;
    }

    char line[512];
    while (fgets(line, sizeof(line), f)) {
        // Hapus newline dan whitespace
        size_t len = strlen(line);
        while (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r' || line[len-1] == ' '))
            line[--len] = 0;
        if (len == 0) continue;

        // Cek apakah file ada di SD card
        // Format: bisa full path atau nama file saja
        std::string filepath;
        struct stat st;

        if (line[0] == '/' || strncmp(line, "sdmc:", 5) == 0) {
            filepath = line;
        } else {
            filepath = std::string(GAMES_DIR) + line;
        }

        GameEntry entry;
        entry.filename   = line;
        entry.filepath   = filepath;
        entry.done       = false;
        entry.retryCount = 0;
        s_queue.push_back(entry);
    }

    fclose(f);

    s_totalGames = (int)s_queue.size();
    s_doneGames  = 0;
    s_failedGames = 0;

    return s_totalGames > 0;
}

static std::vector<std::string> getPendingFilenames() {
    std::vector<std::string> result;
    for (const auto& g : s_queue) {
        if (!g.done && g.retryCount < MAX_RETRY) {
            result.push_back(g.filename);
        }
    }
    return result;
}

static GameEntry* findByFilename(const std::string& filename) {
    for (auto& g : s_queue) {
        if (g.filename == filename) return &g;
    }
    return nullptr;
}

static void markDone(const std::string& filename) {
    GameEntry* g = findByFilename(filename);
    if (g) {
        g->done = true;
        s_doneGames++;
    }
}

static void markFailed(const std::string& filename) {
    GameEntry* g = findByFilename(filename);
    if (g) {
        g->retryCount++;
        if (g->retryCount >= MAX_RETRY) {
            g->done = true;  // berhenti retry
            s_failedGames++;
            s_doneGames++;
        }
    }
}

// ─── DBI Session ─────────────────────────────────────────────────────────────

static bool runSession() {
    // Kirim LIST ke host
    auto pending = getPendingFilenames();
    if (pending.empty()) return true;  // semua sudah done

    printProgress(s_doneGames, s_totalGames, "Mengirim daftar game...");

    UsbResult r = dbiSendList(pending);
    if (r != USB_OK) {
        printStatus("Gagal kirim list!", COLOR_RED);
        return false;
    }

    // Loop terima FILE_RANGE request
    std::string currentFile;
    uint64_t    currentOffset = 0;

    while (s_running) {
        // Cek input user
        hidScanInput();
        u64 kDown = hidKeysDown(CONTROLLER_P1_AUTO);
        if (kDown & KEY_PLUS) {
            s_running = false;
            break;
        }

        // Baca header dari host
        DbiHeader hdr;
        r = dbiReadHeader(&hdr);
        if (r == USB_TIMEOUT) continue;
        if (r != USB_OK) return false;

        if (hdr.id == CMD_ID_EXIT) {
            // Host selesai
            dbiSendExit();
            return true;
        }

        if (hdr.id == CMD_ID_LIST) {
            // Host minta list lagi (reconnect)
            pending = getPendingFilenames();
            r = dbiSendList(pending);
            if (r != USB_OK) return false;
            continue;
        }

        if (hdr.id == CMD_ID_FILE_RANGE) {
            // Kirim ACK dulu
            r = dbiSendHeader(CMD_TYPE_ACK, CMD_ID_FILE_RANGE, hdr.dataSize);
            if (r != USB_OK) return false;

            // Baca request detail
            FileRangeRequest req;
            memset(&req, 0, sizeof(req));
            r = usbRead(&req, hdr.dataSize, 5000000000ULL);
            if (r != USB_OK) return false;

            std::string filename = req.filename;
            uint64_t    offset   = req.rangeOffset;
            uint32_t    size     = req.rangeSize;

            // Tracking file yang sedang ditransfer
            if (filename != currentFile || offset < currentOffset) {
                currentFile   = filename;
                currentOffset = 0;
            }
            currentOffset = offset + size;

            printProgress(s_doneGames, s_totalGames, filename);

            // Cari filepath
            GameEntry* entry = findByFilename(filename);
            if (!entry) {
                // File tidak dikenal, skip
                r = dbiSendHeader(CMD_TYPE_RESPONSE, CMD_ID_FILE_RANGE, 0);
                continue;
            }

            // Kirim data file
            r = dbiSendFileData(entry->filepath.c_str(), offset, size);
            if (r != USB_OK) {
                markFailed(filename);
                printStatus("Transfer gagal, akan diretry!", COLOR_RED);
                svcSleepThread(1000000000ULL);
                return false;  // reconnect
            }

            // Cek apakah ini chunk terakhir dari file
            struct stat st;
            if (stat(entry->filepath.c_str(), &st) == 0) {
                if (offset + size >= (uint64_t)st.st_size) {
                    markDone(filename);
                }
            }

            continue;
        }

        // Unknown command, abaikan
    }

    return true;
}

// ─── Main ────────────────────────────────────────────────────────────────────

int main(int argc, char* argv[]) {
    // Init console
    consoleInit(nullptr);
    clearScreen();
    printHeader();

    // Init services
    Result rc;
    rc = nsInitialize();
    if (R_FAILED(rc)) {
        printStatus("Gagal init NS service!", COLOR_RED);
        printStatus("Pastikan homebrew dijalankan dengan benar.", COLOR_WHITE);
        consoleUpdate(nullptr);
        svcSleepThread(3000000000ULL);
        consoleExit(nullptr);
        return 1;
    }

    // Load queue
    printStatus("Membaca queue...", COLOR_CYAN);
    consoleUpdate(nullptr);

    if (!loadQueue()) {
        printStatus("\nTidak ada game di queue.", COLOR_YELLOW);
        printStatus("Isi queue dari dashboard web NAS dulu.", COLOR_WHITE);
        consoleUpdate(nullptr);

        // Tunggu user tekan +
        while (appletMainLoop()) {
            hidScanInput();
            if (hidKeysDown(CONTROLLER_P1_AUTO) & KEY_PLUS) break;
        }

        nsExit();
        consoleExit(nullptr);
        return 0;
    }

    printf(COLOR_GREEN "Queue loaded: %d game\n" COLOR_RESET, s_totalGames);
    printf(COLOR_YELLOW "Menghubungkan ke NAS via USB...\n" COLOR_RESET);
    consoleUpdate(nullptr);

    // Init USB dan tunggu konek ke NAS
    int usbRetry = 0;
    while (s_running && appletMainLoop()) {
        hidScanInput();
        if (hidKeysDown(CONTROLLER_P1_AUTO) & KEY_PLUS) break;

        UsbResult r = usbInit();
        if (r == USB_OK) {
            printf(COLOR_GREEN "USB terhubung!\n" COLOR_RESET);
            consoleUpdate(nullptr);
            svcSleepThread(500000000ULL);
            break;
        }

        usbRetry++;
        printf(COLOR_YELLOW "Menunggu koneksi USB... (%d)\n" COLOR_RESET, usbRetry);
        consoleUpdate(nullptr);
        svcSleepThread(1000000000ULL);
    }

    // Main loop — session + auto retry
    int sessionRetry = 0;
    while (s_running && appletMainLoop()) {
        hidScanInput();
        if (hidKeysDown(CONTROLLER_P1_AUTO) & KEY_PLUS) break;

        // Cek apakah semua sudah selesai
        auto pending = getPendingFilenames();
        if (pending.empty()) {
            clearScreen();
            printHeader();
            printf(COLOR_GREEN COLOR_BOLD
                   "✓ Semua game selesai diinstall!\n\n" COLOR_RESET);
            printf("Total  : %d game\n", s_totalGames);
            printf("Sukses : %d game\n", s_doneGames - s_failedGames);
            printf("Gagal  : %d game\n", s_failedGames);
            printf("\n" COLOR_WHITE "(+) Keluar\n" COLOR_RESET);
            consoleUpdate(nullptr);

            // Tunggu user tekan +
            while (appletMainLoop()) {
                hidScanInput();
                if (hidKeysDown(CONTROLLER_P1_AUTO) & KEY_PLUS) break;
            }
            break;
        }

        // Jalankan session
        bool ok = runSession();
        if (!ok) {
            sessionRetry++;
            if (sessionRetry > 10) {
                printStatus("Terlalu banyak error, hentikan.", COLOR_RED);
                break;
            }
            // Reconnect USB
            usbExit();
            printProgress(s_doneGames, s_totalGames, "Reconnecting...");
            svcSleepThread(2000000000ULL);
            usbInit();
        } else {
            sessionRetry = 0;
        }
    }

    usbExit();
    nsExit();
    consoleExit(nullptr);
    return 0;
}
