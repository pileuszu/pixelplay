import win32gui
import time

def rename_game_window():
    target_title = "컴프야V26"
    temp_title = "컴프야V26_1"
    
    hwnd = win32gui.FindWindow(None, target_title)
    if hwnd:
        print(f"[+] 찾음: {target_title} (HWND: {hwnd})")
        win32gui.SetWindowText(hwnd, temp_title)
        print(f"[+] 첫 번째 창 이름을 '{temp_title}'로 변경했습니다.")
        print("\n이제 파워쉘이나 CMD에서 두 번째 게임을 실행해 보세요!")
        print("두 번째 게임이 정상 실행되면, 이 창에서 엔터(Enter)를 누르세요.")
        
        input("\n두 번째 게임이 켜졌다면 엔터를 누르세요 (이름 복구)...")
        
        # 새롭게 켜진 두 번째 창이 있으면 그것도 이름을 바꿔서 구분하기 쉽게 만듭니다.
        hwnd2 = win32gui.FindWindow(None, target_title)
        if hwnd2:
            win32gui.SetWindowText(hwnd2, "컴프야V26_2")
            print(f"[+] 두 번째 창 이름을 '컴프야V26_2'로 설정했습니다.")
        else:
            print("[-] 두 번째 창을 찾지 못했습니다.")
            
        # 첫 번째 창 이름도 안전하게 유지하거나 필요시 복구
        print("[+] 창 이름 설정 완료.")
    else:
        print(f"[-] '{target_title}' 창을 찾을 수 없습니다. 첫 번째 게임을 먼저 켜주세요.")

if __name__ == "__main__":
    rename_game_window()
