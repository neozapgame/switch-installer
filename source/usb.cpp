#include "usb.h"
#include <cstring>
#include <cstdio>

#define USB_TIMEOUT_NS  (5000000000ULL)   // 5 detik
#define USB_RETRY_NS    (100000000ULL)    // 100ms

static UsbDsInterface*  s_interface = nullptr;
static UsbDsEndpoint*   s_endpointIn  = nullptr;
static UsbDsEndpoint*   s_endpointOut = nullptr;

// USB descriptor setup (device mode, bulk transfer)
static const u8 s_manufacturerStr[] = {
    0x10, 0x03, 'N',0,'e',0,'o',0,'Z',0,'a',0,'p',0
};
static const u8 s_productStr[] = {
    0x1A, 0x03, 'S',0,'w',0,'i',0,'t',0,'c',0,'h',0,
    'I',0,'n',0,'s',0,'t',0,'a',0,'l',0,'l',0
};

UsbResult usbInit() {
    Result rc;

    // Init USB device mode
    rc = usbDsInitialize();
    if (R_FAILED(rc)) return USB_ERROR;

    // Setup interface
    struct usb_interface_descriptor ifaceDesc = {};
    ifaceDesc.bLength            = USB_DT_INTERFACE_SIZE;
    ifaceDesc.bDescriptorType    = USB_DT_INTERFACE;
    ifaceDesc.bInterfaceNumber   = 0;
    ifaceDesc.bNumEndpoints      = 2;
    ifaceDesc.bInterfaceClass    = USB_CLASS_VENDOR_SPEC;
    ifaceDesc.bInterfaceSubClass = USB_CLASS_VENDOR_SPEC;
    ifaceDesc.bInterfaceProtocol = USB_CLASS_VENDOR_SPEC;

    rc = usbDsGetDsInterface(&s_interface, &ifaceDesc, "usb");
    if (R_FAILED(rc)) { usbDsExit(); return USB_ERROR; }

    // Bulk OUT endpoint (host -> switch, kita baca)
    struct usb_endpoint_descriptor epOutDesc = {};
    epOutDesc.bLength          = USB_DT_ENDPOINT_SIZE;
    epOutDesc.bDescriptorType  = USB_DT_ENDPOINT;
    epOutDesc.bEndpointAddress = USB_ENDPOINT_OUT;
    epOutDesc.bmAttributes     = USB_TRANSFER_TYPE_BULK;
    epOutDesc.wMaxPacketSize   = 0x200;
    epOutDesc.bInterval        = 0;

    rc = usbDsInterface_GetDsEndpoint(s_interface, &s_endpointOut, &epOutDesc);
    if (R_FAILED(rc)) { usbDsExit(); return USB_ERROR; }

    // Bulk IN endpoint (switch -> host, kita tulis)
    struct usb_endpoint_descriptor epInDesc = {};
    epInDesc.bLength          = USB_DT_ENDPOINT_SIZE;
    epInDesc.bDescriptorType  = USB_DT_ENDPOINT;
    epInDesc.bEndpointAddress = USB_ENDPOINT_IN;
    epInDesc.bmAttributes     = USB_TRANSFER_TYPE_BULK;
    epInDesc.wMaxPacketSize   = 0x200;
    epInDesc.bInterval        = 0;

    rc = usbDsInterface_GetDsEndpoint(s_interface, &s_endpointIn, &epInDesc);
    if (R_FAILED(rc)) { usbDsExit(); return USB_ERROR; }

    rc = usbDsInterface_EnableInterface(s_interface);
    if (R_FAILED(rc)) { usbDsExit(); return USB_ERROR; }

    rc = usbDsEnable();
    if (R_FAILED(rc)) { usbDsExit(); return USB_ERROR; }

    // Tunggu host konek
    u32 state = 0;
    u64 elapsed = 0;
    while (state != 5) {  // 5 = configured
        usbDsGetState(&state);
        svcSleepThread(USB_RETRY_NS);
        elapsed += USB_RETRY_NS;
        if (elapsed > USB_TIMEOUT_NS * 3) return USB_TIMEOUT;
        if (!appletMainLoop()) return USB_DISCONNECTED;
    }

    return USB_OK;
}

void usbExit() {
    usbDsExit();
}

UsbResult usbRead(void* buf, size_t size, u64 timeoutNs) {
    UsbDsReportData reportData;
    u32 urbId, transferred;
    Result rc;

    rc = usbDsEndpoint_PostBufferAsync(s_endpointOut, buf, size, &urbId);
    if (R_FAILED(rc)) return USB_ERROR;

    rc = eventWait(&s_endpointOut->CompletionEvent, timeoutNs);
    eventClear(&s_endpointOut->CompletionEvent);
    if (R_FAILED(rc)) {
        usbDsEndpoint_Cancel(s_endpointOut);
        return USB_TIMEOUT;
    }

    rc = usbDsEndpoint_GetReportData(s_endpointOut, &reportData);
    if (R_FAILED(rc)) return USB_ERROR;

    rc = usbDsReportData_GetReportCount(&reportData, &transferred);
    if (R_FAILED(rc) || transferred == 0) return USB_ERROR;

    return USB_OK;
}

UsbResult usbWrite(const void* buf, size_t size, u64 timeoutNs) {
    UsbDsReportData reportData;
    u32 urbId, transferred;
    Result rc;

    rc = usbDsEndpoint_PostBufferAsync(s_endpointIn, (void*)buf, size, &urbId);
    if (R_FAILED(rc)) return USB_ERROR;

    rc = eventWait(&s_endpointIn->CompletionEvent, timeoutNs);
    eventClear(&s_endpointIn->CompletionEvent);
    if (R_FAILED(rc)) {
        usbDsEndpoint_Cancel(s_endpointIn);
        return USB_TIMEOUT;
    }

    rc = usbDsEndpoint_GetReportData(s_endpointIn, &reportData);
    if (R_FAILED(rc)) return USB_ERROR;

    return USB_OK;
}

UsbResult dbiSendHeader(CmdType type, CmdId id, uint32_t dataSize) {
    DbiHeader hdr;
    memcpy(hdr.magic, DBI_MAGIC, 4);
    hdr.type     = (uint32_t)type;
    hdr.id       = (uint32_t)id;
    hdr.dataSize = dataSize;
    return usbWrite(&hdr, sizeof(hdr), USB_TIMEOUT_NS);
}

UsbResult dbiReadHeader(DbiHeader* hdr) {
    UsbResult r = usbRead(hdr, sizeof(DbiHeader), USB_TIMEOUT_NS);
    if (r != USB_OK) return r;
    if (memcmp(hdr->magic, DBI_MAGIC, 4) != 0) return USB_ERROR;
    return USB_OK;
}

UsbResult dbiSendList(const std::vector<std::string>& filenames) {
    // Gabungkan semua filename dengan newline
    std::string list;
    for (const auto& f : filenames) {
        list += f;
        list += "\n";
    }
    if (!list.empty()) list.pop_back(); // hapus newline terakhir

    uint32_t dataSize = (uint32_t)list.size();

    // Kirim response header
    UsbResult r = dbiSendHeader(CMD_TYPE_RESPONSE, CMD_ID_LIST, dataSize);
    if (r != USB_OK) return r;

    // Baca ACK dari host
    DbiHeader ack;
    r = dbiReadHeader(&ack);
    if (r != USB_OK) return r;

    // Kirim data list
    return usbWrite(list.c_str(), dataSize, USB_TIMEOUT_NS);
}

UsbResult dbiReadFileRangeRequest(FileRangeRequest* req) {
    // Baca header FILE_RANGE dari host
    DbiHeader hdr;
    UsbResult r = dbiReadHeader(&hdr);
    if (r != USB_OK) return r;
    if (hdr.id != CMD_ID_FILE_RANGE) return USB_ERROR;

    // Kirim ACK
    r = dbiSendHeader(CMD_TYPE_ACK, CMD_ID_FILE_RANGE, hdr.dataSize);
    if (r != USB_OK) return r;

    // Baca request data
    return usbRead(req, hdr.dataSize, USB_TIMEOUT_NS);
}

UsbResult dbiSendFileData(const char* filepath, uint64_t offset, uint32_t size) {
    // Kirim response header
    UsbResult r = dbiSendHeader(CMD_TYPE_RESPONSE, CMD_ID_FILE_RANGE, size);
    if (r != USB_OK) return r;

    // Baca ACK
    DbiHeader ack;
    r = dbiReadHeader(&ack);
    if (r != USB_OK) return r;

    // Buka file dan kirim data per chunk
    FILE* f = fopen(filepath, "rb");
    if (!f) return USB_ERROR;

    fseeko(f, (off_t)offset, SEEK_SET);

    static u8 buf[BUFFER_SIZE] __attribute__((aligned(0x1000)));
    uint32_t sent = 0;
    while (sent < size) {
        uint32_t chunk = size - sent;
        if (chunk > BUFFER_SIZE) chunk = BUFFER_SIZE;

        size_t rd = fread(buf, 1, chunk, f);
        if (rd == 0) break;

        r = usbWrite(buf, rd, USB_TIMEOUT_NS * 2);
        if (r != USB_OK) { fclose(f); return r; }

        sent += (uint32_t)rd;
    }

    fclose(f);
    return (sent == size) ? USB_OK : USB_ERROR;
}

UsbResult dbiSendExit() {
    return dbiSendHeader(CMD_TYPE_RESPONSE, CMD_ID_EXIT, 0);
}
