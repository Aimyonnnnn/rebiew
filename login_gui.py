import requests
import sys
import json
import os
import traceback
from PyQt5.QtWidgets import (
    QDialog, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel, 
    QMessageBox, QTextEdit, QApplication, QFrame
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt
from datetime import datetime, timedelta

from multi_account_gui import MultiAccountGUI

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

class LoginWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("사용자 로그인")
        self.setFixedSize(500, 850)
        self.setStyleSheet("""
            QDialog {
                background-color: #FFFFFF;
            }
            QLineEdit, QTextEdit {
                padding: 12px;
                border: 1px solid #DEE2E6;
                border-radius: 8px;
                font-size: 14px;
                background-color: #F8F9FA;
                color: #212529;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 2px solid #0D6EFD;
                background-color: #FFFFFF;
            }
            QPushButton {
                padding: 12px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                color: #FFFFFF;
                background-color: #0D6EFD;
                border: none;
            }
            QPushButton:hover {
                background-color: #0B5ED7;
            }
            QLabel {
                font-size: 14px;
                color: #495057;
            }
            QFrame {
                background-color: #E9ECEF;
                border-radius: 8px;
                padding: 10px;
                border: 1px solid #ced4da;
            }
        """)

        font = QFont("Segoe UI", 11, QFont.Bold)

        title_label = QLabel("사용자 로그인")
        title_label.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("background-color: #FFD700; color: #212529; margin: 15px 0; padding: 10px; border-radius: 8px;")

        self.id_input = QLineEdit()
        self.id_input.setText("threads12")
        self.id_input.setFont(font)
        self.id_input.setReadOnly(True)

        self.pw_input = QLineEdit()
        self.pw_input.setText("9807161223")
        self.pw_input.setEchoMode(QLineEdit.Password)
        self.pw_input.setFont(font)
        self.pw_input.setReadOnly(True)

        self.login_btn = QPushButton("로그인")
        self.login_btn.setFont(font)
        self.login_btn.clicked.connect(self.try_login)

        self.ip_label = QLabel("내 접속 IP: 확인 중...")
        self.ip_label.setFont(font)
        self.ip_label.setFixedHeight(35)
        self.expiry_label = QLabel("남은 사용 기간: 확인 중...")
        self.expiry_label.setFont(font)
        self.expiry_label.setFixedHeight(35)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFont(QFont("Consolas", 11))
        self.log_area.setFixedHeight(180)

        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.addWidget(title_label)
        layout.addWidget(QLabel("아이디"))
        layout.addWidget(self.id_input)
        layout.addWidget(QLabel("비밀번호"))
        layout.addWidget(self.pw_input)
        layout.addWidget(self.login_btn)

        info_frame = QFrame()
        info_layout = QVBoxLayout()
        info_layout.addWidget(self.ip_label)
        info_layout.addWidget(self.expiry_label)
        info_frame.setLayout(info_layout)
        layout.addWidget(info_frame)

        layout.addWidget(QLabel("로그"))
        layout.addWidget(self.log_area)
        layout.addStretch()

        self.setLayout(layout)
        self.login_success = False
        self.fetch_ip()

    def log(self, message):
        self.log_area.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

    def fetch_ip(self):
        try:
            response = requests.get("https://api.ipify.org?format=json")
            ip = response.json().get("ip", "알 수 없음")
            self.ip_label.setText(f"📡 내 접속 IP: {ip}")
            self.log(f"📡 내 접속 IP: {ip}")

            # IP 기반 만료일 요청
            expiry_res = requests.get(
                "https://port-0-fnlunasea-m66s84vua61720dd.sel4.cloudtype.app/api/expiry_by_ip",
                headers={"X-Forwarded-For": ip}
            )
            expiry_data = expiry_res.json()
            if expiry_data.get("success"):
                expiry_date = expiry_data.get("expiry_date")
                self.update_expiry_info(expiry_date)
                self.log(f"✅ IP기반 사용기간 조회 성공: {expiry_date}")
                return  # 성공 시 except로 안 넘어가게!
            else:
                self.expiry_label.setText("남은 사용 기간: 확인 불가")
                self.log("남은 사용 기간: 확인 불가")

        except Exception as e:
            self.expiry_label.setText("남은 사용 기간: 확인 불가")
            self.log("남은 사용 기간: 확인 불가")

    def try_login(self):
        username = self.id_input.text().strip()
        password = self.pw_input.text().strip()

        if not username or not password:
            self.log("🚫 아이디와 비밀번호를 입력하세요.")
            QMessageBox.warning(self, "입력 오류", "아이디와 비밀번호를 입력하세요.")
            return

        self.log("\n🚀 로그인 시도 중...")
        self.log(f"입력 ID: {username}")

        try:
            response = requests.post(
                "https://port-0-fnlunasea-m66s84vua61720dd.sel4.cloudtype.app/api/login",
                json={"username": username, "password": password}
            )
            data = response.json()

            if data.get("success"):
                self.log("✅ 로그인 성공")
                expiry_date = data.get("expiry_date", "정보 없음")
                client_ip = data.get("client_ip", "정보 없음")
                self.log(f"📅 만료일: {expiry_date}")
                self.log(f"📡 현재 접속 IP: {client_ip}")
                self.update_expiry_info(expiry_date)
                QMessageBox.information(self, "로그인 성공", data.get("message", "성공"))
                self.login_success = True
                self.accept()
            else:
                self.log("❌ 로그인 실패")
                self.log(f"사유: {data.get('message')}")
                self.log(f"📡 현재 접속 IP: {data.get('client_ip')}")
                QMessageBox.warning(self, "로그인 실패", data.get("message", "로그인 실패"))
        except Exception as e:
            self.log(f"🚫 서버 연결 오류: {e}")
            QMessageBox.critical(self, "오류", f"서버 연결 실패: {e}")

    def update_expiry_info(self, expiry_date):
        try:
            try:
                expiry = datetime.fromisoformat(expiry_date)
            except Exception:
                expiry = datetime.strptime(expiry_date, "%Y-%m-%dT%H:%M")
            now = datetime.now()
            delta = expiry - now
            days = delta.days
            hours, remainder = divmod(delta.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            msg = f"남은 사용 기간: {days}일 {hours}시간 {minutes}분 {seconds}초"
            self.expiry_label.setText(msg)
            self.log(msg)
        except Exception:
            msg = "남은 사용 기간: 확인 불가"
            self.expiry_label.setText(msg)
            self.log(msg)