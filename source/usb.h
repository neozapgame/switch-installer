#pragma once
#include <switch.h>
#include <string>
#include <vector>

// DBI Protocol constants
#define DBI_MAGIC       "DBI0"
#define BUFFER_SIZE     (1024 * 1024)  // 1MB chunks

// Command types
typedef enum {
    CMD_TYPE_REQUEST  = 0,
    CMD_TYPE_RESPONSE = 1,
    CMD_TYPE_ACK      = 2,
} CmdType;

// Command IDs
typedef enum {
    CMD_ID_EXIT       = 0,
    CMD_ID_LIST       = 3,
    CMD_ID_FILE_RANGE = 2,
} CmdId;

// DBI packet header (16 bytes)
typedef struct {
    char     magic[4];   // "DBI0"
    uint32_t type;       // CmdType
    uint32_t id;         // CmdId
    uint32_t dataSize;   // payload size
} __attribute__((packed)) DbiHeader;

// File range request from Switch
typedef struct {
    uint32_t rangeSize;
    uint64_t rangeOffset;
    uint64_t padding;
    char     filename[512];
} __attribute__((packed)) FileRangeRequest;

// USB connection result
typedef enum {
    USB_OK           = 0,
    USB_ERROR        = 1,
    USB_TIMEOUT      = 2,
    USB_DISCONNECTED = 3,
} UsbResult;

// Init/deinit USB
UsbResult usbInit();
void      usbExit();

// Low-level read/write
UsbResult usbRead(void* buf, size_t size, u64 timeoutNs);
UsbResult usbWrite(const void* buf, size_t size, u64 timeoutNs);

// High-level DBI protocol
UsbResult dbiSendHeader(CmdType type, CmdId id, uint32_t dataSize);
UsbResult dbiReadHeader(DbiHeader* hdr);
UsbResult dbiSendList(const std::vector<std::string>& filenames);
UsbResult dbiReadFileRangeRequest(FileRangeRequest* req);
UsbResult dbiSendFileData(const char* filepath, uint64_t offset, uint32_t size);
UsbResult dbiSendExit();
