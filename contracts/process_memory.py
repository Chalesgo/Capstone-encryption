"""Small, dependency-free current/high-water process memory readings."""
import ctypes
import os


def process_memory_mb():
    """Return current RSS and peak RSS in MiB where the OS exposes them."""
    try:
        values = {}
        with open('/proc/self/status', encoding='ascii') as status_file:
            for line in status_file:
                if line.startswith(('VmRSS:', 'VmHWM:')):
                    key, value, *_ = line.split()
                    values[key[:-1].lower()] = round(int(value) / 1024, 1)
        if values:
            return values
    except (OSError, ValueError):
        pass

    if os.name == 'nt':
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ('cb', ctypes.c_ulong),
                ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t),
            ]

        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            get_memory_info = ctypes.WinDLL('Psapi.dll').GetProcessMemoryInfo
            get_memory_info.argtypes = (
                ctypes.c_void_p,
                ctypes.POINTER(ProcessMemoryCounters),
                ctypes.c_ulong,
            )
            get_memory_info.restype = ctypes.c_int
            if get_memory_info(process, ctypes.byref(counters), counters.cb):
                return {
                    'vmrss': round(counters.WorkingSetSize / (1024 * 1024), 1),
                    'vmhwm': round(counters.PeakWorkingSetSize / (1024 * 1024), 1),
                }
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    return {}
