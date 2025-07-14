# main.py
import sys
import traceback
from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog

# pyinstaller가 놓칠 수 있는 숨겨진 import를 명시적으로 포함합니다.
# 이 부분이 pyinstaller 빌드 실패를 막아줄 수 있습니다.
from login_gui import LoginWindow
from multi_account_gui import MultiAccountGUI

def excepthook(exc_type, exc_value, exc_tb):
    """
    모든 처리되지 않은 예외를 잡아 파일에 기록하고 사용자에게 알리는 전역 핸들러.
    이것이 프로그램이 소리 없이 종료되는 것을 막아줍니다.
    """
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    with open("error.log", "w", encoding="utf-8") as f:
        f.write("처리되지 않은 오류 발생:\n\n")
        f.write(tb)
    
    error_box = QMessageBox()
    error_box.setIcon(QMessageBox.Critical)
    error_box.setText("예기치 않은 오류가 발생했습니다.")
    error_box.setInformativeText("자세한 내용은 error.log 파일을 참조하세요.")
    error_box.setWindowTitle("프로그램 오류")
    error_box.exec_()
    QApplication.quit()

def main():
    # 위에서 정의한 전역 오류 핸들러를 시스템에 설치합니다.
    sys.excepthook = excepthook

    app = QApplication(sys.argv)
    login_window = LoginWindow()
    
    # 로그인 창을 실행하고, 성공적으로 닫혔는지 확인합니다.
    if login_window.exec_() == QDialog.Accepted:
        # MultiAccountGUI를 실행합니다. 여기서 오류가 발생해도 excepthook이 잡아줍니다.
        main_gui = MultiAccountGUI()
        main_gui.show()
        sys.exit(app.exec_())

if __name__ == '__main__':
    main()
