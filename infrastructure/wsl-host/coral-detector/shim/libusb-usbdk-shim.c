/* libusb-1.0.dll shim for libedgetpu on Windows (docs/wsl/coral-detector.md).

   libedgetpu calls libusb_set_option(ctx, LIBUSB_OPTION_USE_USBDK) and treats
   a failure as fatal, so without the UsbDk driver it finds no Edge TPU. UsbDk
   is incompatible with usbipd-win, which the Z-Stick passthrough needs. This
   shim reports success for USE_USBDK, so libusb stays on its WinUSB backend,
   and forwards every other call to the real libusb, installed next to it as
   libusbreal.dll (the forwarders are in the .def file).

   Build: see build.sh. */
#include <windows.h>
#include <stdarg.h>

#define LIBUSB_OPTION_LOG_LEVEL 0
#define LIBUSB_OPTION_USE_USBDK 1
#define LIBUSB_ERROR_OTHER (-99)

typedef int (__cdecl *set_option_fn)(void *ctx, int option, ...);

/* Load libusbreal.dll from this DLL's own directory, so the result does not
   depend on the process's DLL search path or working directory. */
static HMODULE load_real(void)
{
    HMODULE self = NULL;
    wchar_t path[MAX_PATH];
    DWORD n;
    wchar_t *slash;

    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCWSTR)(void *)&load_real, &self))
        return NULL;
    n = GetModuleFileNameW(self, path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH)
        return NULL;
    slash = wcsrchr(path, L'\\');
    if (!slash || (size_t)(slash - path) + 16 >= MAX_PATH)
        return NULL;
    lstrcpyW(slash + 1, L"libusbreal.dll");
    return LoadLibraryExW(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
}

__declspec(dllexport) int __cdecl libusb_set_option(void *ctx, int option, ...)
{
    static set_option_fn real = NULL;

    if (option == LIBUSB_OPTION_USE_USBDK)
        return 0;
    if (!real) {
        HMODULE h = load_real();
        if (!h)
            return LIBUSB_ERROR_OTHER;
        real = (set_option_fn)(void *)GetProcAddress(h, "libusb_set_option");
        if (!real)
            return LIBUSB_ERROR_OTHER;
    }
    if (option == LIBUSB_OPTION_LOG_LEVEL) {
        va_list ap;
        int level;
        va_start(ap, option);
        level = va_arg(ap, int);
        va_end(ap);
        return real(ctx, option, level);
    }
    return real(ctx, option);
}
