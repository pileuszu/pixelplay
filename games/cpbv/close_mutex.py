import ctypes
from ctypes import wintypes
import sys

# Windows API constants and DLLs
ntdll = ctypes.WinDLL('ntdll')
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# Access rights
PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
DUPLICATE_CLOSE_SOURCE = 0x00000001
DUPLICATE_SAME_ACCESS = 0x00000002

class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_wchar_p),
    ]

class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("HandleValue", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectTypeIndex", ctypes.c_ushort),
        ("HandleAttributes", ctypes.c_ulong),
        ("Reserved", ctypes.c_ulong),
    ]

# Function prototypes
ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)
]
ntdll.NtQuerySystemInformation.restype = ctypes.c_long

ntdll.NtQueryObject.argtypes = [
    wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)
]
ntdll.NtQueryObject.restype = ctypes.c_long

def get_pids_by_name(target_name):
    # Using EnumProcesses to find PIDs of target process name
    # We want to be independent of external libraries
    try:
        import win32process
        import win32api
        import win32con
        pids = win32process.EnumProcesses()
        target_pids = []
        for pid in pids:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
                name = win32process.GetModuleFileNameEx(h, 0)
                if target_name.lower() in name.lower():
                    target_pids.append(pid)
            except:
                pass
        return target_pids
    except ImportError:
        # Fallback to ctypes Process32First/Next if pywin32 is not fully working
        pids = []
        TH32CS_SNAPPROCESS = 0x00000002
        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)
            ]
        h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h_snap == -1:
            return []
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
            while True:
                if target_name.lower() in pe.szExeFile.lower():
                    pids.append(pe.th32ProcessID)
                if not kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(h_snap)
        return pids

def close_mutants_in_process(pid):
    print(f"\n[+] PID {pid} 프로세스의 뮤텍스 핸들 검색 중...")
    
    # Open remote process with duplicate handle permission
    h_proc = kernel32.OpenProcess(PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION, False, pid)
    if not h_proc:
        print(f"[-] 프로세스 열기 실패 (PID: {pid}). 관리자 권한으로 실행했는지 확인하세요.")
        return 0
        
    try:
        # Get all handles in the system
        # SystemExtendedHandleInformation = 64
        size = 1024 * 1024
        buf = None
        while True:
            buf = ctypes.create_string_buffer(size)
            returned = ctypes.c_ulong(0)
            status = ntdll.NtQuerySystemInformation(64, buf, size, ctypes.byref(returned))
            if status == 0:
                break
            elif status == 0xC0000004 or status == -1073741820: # STATUS_INFO_LENGTH_MISMATCH
                size = max(size * 2, returned.value)
            else:
                print(f"[-] 핸들 정보 조회 실패: {hex(status & 0xffffffff)}")
                return 0
                
        num_handles = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint64)).contents.value
        handles_offset = 16
        entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
        
        closed_count = 0
        for i in range(num_handles):
            offset = handles_offset + i * entry_size
            entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_buffer_copy(buf, offset)
            
            # Filter handles owned by our target process
            if int(entry.UniqueProcessId) != pid:
                continue
                
            h_val = entry.HandleValue
            
            # Duplicate the handle to our python process to query its type
            h_dup = wintypes.HANDLE()
            success = kernel32.DuplicateHandle(
                h_proc,
                h_val,
                kernel32.GetCurrentProcess(),
                ctypes.byref(h_dup),
                0,
                False,
                0 # No special options, just copy
            )
            
            if success:
                # Query object type name
                # ObjectTypeInformation = 2
                t_size = 1024
                t_buf = ctypes.create_string_buffer(t_size)
                t_returned = ctypes.c_ulong(0)
                t_status = ntdll.NtQueryObject(h_dup, 2, t_buf, t_size, ctypes.byref(t_returned))
                
                type_name = ""
                if t_status == 0:
                    uni_str = UNICODE_STRING.from_buffer(t_buf)
                    type_name = uni_str.Buffer
                
                kernel32.CloseHandle(h_dup) # Close the duplicate copy in Python
                
                # If it's a Mutant (Mutex), close it in the target process!
                if type_name == "Mutant":
                    # Duplicate with DUPLICATE_CLOSE_SOURCE to close the handle in the source process
                    h_close = wintypes.HANDLE()
                    success_close = kernel32.DuplicateHandle(
                        h_proc,
                        h_val,
                        kernel32.GetCurrentProcess(),
                        ctypes.byref(h_close),
                        0,
                        False,
                        DUPLICATE_CLOSE_SOURCE | DUPLICATE_SAME_ACCESS
                    )
                    if success_close:
                        kernel32.CloseHandle(h_close) # Close our copy, leaving it closed in target
                        print(f"    [✔] Mutant 핸들 해제 성공! (Handle Value: {hex(h_val)})")
                        closed_count += 1
                    else:
                        err = kernel32.GetLastError()
                        print(f"    [x] Mutant 핸들 해제 실패 (Handle Value: {hex(h_val)}), Error: {err}")
                        
        return closed_count
    finally:
        kernel32.CloseHandle(h_proc)

def main():
    # Find PIDs of both CPBV.exe and CPBV2.exe
    target_names = ["CPBV.exe", "CPBV2.exe"]
    found_any = False
    
    for name in target_names:
        pids = get_pids_by_name(name)
        for pid in pids:
            found_any = True
            closed = close_mutants_in_process(pid)
            print(f"[+] {name} (PID: {pid})에서 총 {closed}개의 뮤텍스를 해제했습니다.")
            
    if not found_any:
        print("[-] 실행 중인 컴프야V26 게임 프로세스를 찾을 수 없습니다. 게임을 먼저 실행해 주세요.")

if __name__ == "__main__":
    main()
