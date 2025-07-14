import sys
import json
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QTableWidget, QTableWidgetItem, 
                            QPushButton, QTextEdit, QLabel, QGroupBox, 
                            QProgressBar, QTabWidget, QGridLayout, QLineEdit,
                            QComboBox, QSpinBox, QCheckBox, QSplitter,
                            QHeaderView, QFrame, QScrollArea, QDialog,
                            QFileDialog, QMessageBox, QTextBrowser, QFormLayout,
                            QRadioButton, QSizePolicy, QDialogButtonBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QObject
from PyQt5.QtGui import QFont, QColor, QPalette, QIcon, QPixmap, QClipboard

from datetime import datetime
import threading
import time
import pandas as pd
import openpyxl
import zipfile
import threads_api_helper as threads_api
import threads_carousel_helper as carousel_api
import catbox_uploader
from concurrent.futures import ThreadPoolExecutor
import requests
import re
from urllib.parse import urlparse, parse_qs
import atexit
import signal
import pathlib
from pathlib import Path
import hashlib
import random
from playwright.sync_api import Playwright, sync_playwright, Page, BrowserContext
# --- 추가: 시스템 자원 모니터링용 ---
try:
    import psutil
except ImportError:
    psutil = None


class CrashLogger:
    """비정상 종료 시에만 로그를 저장하는 클래스"""
    def __init__(self):
        self.log_messages = []
        self.crash_log_file = None
        self.normal_exit = False
        self.setup_crash_handlers()
    
    def setup_crash_handlers(self):
        """크래시 핸들러 설정"""
        # 정상 종료 시 실행될 함수 등록
        atexit.register(self.cleanup_on_exit)
        
        # 시그널 핸들러 등록 (Windows에서도 지원되는 시그널만)
        try:
            signal.signal(signal.SIGINT, self.signal_handler)  # Ctrl+C
            signal.signal(signal.SIGTERM, self.signal_handler)  # 종료 시그널
            if hasattr(signal, 'SIGBREAK'):  # Windows 전용
                signal.signal(signal.SIGBREAK, self.signal_handler)
        except Exception:
            pass  # 시그널 설정 실패 시 무시
    
    def signal_handler(self, signum, frame):
        """시그널 핸들러"""
        self.add_log(f"⚠️ 시그널 {signum} 감지 - 비정상 종료")
        self.save_crash_log()
        sys.exit(1)
    
    def add_log(self, message):
        """로그 메시지 추가"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {message}"
        self.log_messages.append(log_entry)
        
        # 메모리 사용량 제한 (최대 1000개 로그만 유지)
        if len(self.log_messages) > 1000:
            self.log_messages = self.log_messages[-800:]  # 최근 800개만 유지
    
    def save_crash_log(self):
        """크래시 로그 저장"""
        try:
            if not self.log_messages:
                return

            # log 폴더 생성 (없으면)
            log_folder = "log"
            if not os.path.exists(log_folder):
                os.makedirs(log_folder, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            self.crash_log_file = os.path.join(log_folder, f"crash_log_{timestamp}.txt")

            with open(self.crash_log_file, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write(f"🚨 무한 스레드 프로그램 비정상 종료 로그\n")
                f.write(f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 60 + "\n\n")

                for log_entry in self.log_messages:
                    f.write(log_entry + "\n")

                f.write("\n" + "=" * 60 + "\n")
                f.write("🔍 문제 해결을 위해 이 로그를 개발자에게 전달해주세요.\n")
                f.write("=" * 60 + "\n")

            print(f"🚨 비정상 종료 감지! 로그가 저장되었습니다: {self.crash_log_file}")

        except Exception as e:
            print(f"❌ 크래시 로그 저장 실패: {e}")
    
    def mark_normal_exit(self):
        """정상 종료 표시"""
        self.normal_exit = True
        self.add_log("✅ 프로그램 정상 종료")
    
    def cleanup_on_exit(self):
        """종료 시 정리 작업"""
        if self.normal_exit:
            # 정상 종료 시에는 임시 로그 파일만 정리하고 크래시 로그는 저장하지 않음
            if self.crash_log_file and os.path.exists(self.crash_log_file):
                try:
                    os.remove(self.crash_log_file)
                except:
                    pass
        else:
            # 비정상 종료 시에만 로그 저장
            self.add_log("🚨 비정상 종료 감지")
            self.save_crash_log()


# 전역 크래시 로거 인스턴스
crash_logger = CrashLogger()

# 스하리 관련 상수 및 유틸리티 함수
THREADS_URL = "https://www.threads.com/?hl=ko"
SESSION_STORE = {}

def sanitize_folder_name(name):
    """폴더명에서 사용할 수 없는 문자 제거"""
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def generate_account_hash(email, password):
    """계정 정보로 해시 생성"""
    return hashlib.md5(f"{email}:{password}".encode()).hexdigest()

def get_session_dir(email, password):
    """계정별 세션 디렉토리 경로 반환"""
    account_hash = generate_account_hash(email, password)
    safe_email = sanitize_folder_name(email)
    return os.path.join("chrome_profiles", safe_email)

def is_login_required(page):
    """로그인 필요 여부 확인"""
    try:
        # 로그인 버튼이 있는지 확인
        login_button = page.get_by_role('button', name='로그인')
        return login_button.is_visible()
    except:
        return False

def perform_login(page, email, password):
    """로그인 수행"""
    try:
        # 이메일 입력
        email_input = page.get_by_label('전화번호, 사용자 이름 또는 이메일')
        email_input.fill(email)
        
        # 비밀번호 입력
        password_input = page.get_by_label('비밀번호')
        password_input.fill(password)
        
        # 로그인 버튼 클릭
        login_button = page.get_by_role('button', name='로그인')
        login_button.click()
        
        # 로그인 완료 대기
        page.wait_for_timeout(5000)
        
    except Exception as e:
        raise Exception(f"로그인 실패: {str(e)}")

def launch_user_context(playwright, email, password, proxy_server=None, proxy_username=None, proxy_password=None):
    """사용자별 브라우저 컨텍스트 생성 (무조건 크롬 모드)"""
    session_path = get_session_dir(email, password)
    
    browser_args = [
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-blink-features=AutomationControlled',
        '--disable-extensions-except=',
        '--disable-plugins-discovery',
        '--disable-default-apps',
        '--disable-sync',
        '--disable-translate',
        '--hide-scrollbars',
        '--mute-audio',
        '--no-first-run',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-features=TranslateUI',
        '--disable-ipc-flooding-protection',
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    
    if proxy_server:
        browser_args.append(f'--proxy-server={proxy_server}')
    
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=session_path,
        headless=False,  # 무조건 크롬 모드
        args=browser_args
    )
    
    return context

def get_random_comment(comments_text):
    """댓글 텍스트에서 랜덤 댓글 선택"""
    if not comments_text.strip():
        return None
    
    comments = [comment.strip() for comment in comments_text.split('\n') if comment.strip()]
    if not comments:
        return None
    
    return random.choice(comments)

def parse_range(range_str):
    """범위 문자열 파싱 (예: "1-3" -> (1, 3))"""
    try:
        if '-' in range_str:
            min_val, max_val = map(int, range_str.split('-'))
            return min_val, max_val
        else:
            val = int(range_str)
            return val, val
    except:
        return 0, 0


class SettingsDialog(QDialog):
    """설정창 다이얼로그"""
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 상세 설정")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        form_group = QGroupBox("작업 설정")
        form_layout = QFormLayout(form_group)
        
        self.repeat_interval_input = QSpinBox()
        self.repeat_interval_input.setRange(0, 1440) # 0 to 24 hours in minutes
        self.repeat_interval_input.setSuffix(" 분 (0 = 반복 없음)")
        self.repeat_interval_input.setValue(current_settings.get('repeat_interval', 0))
        form_layout.addRow("글쓰기 반복 작업 주기:", self.repeat_interval_input)
        
        self.auto_delete_checkbox = QCheckBox("작업 완료 된 게시글 목록에서 자동 삭제")
        self.auto_delete_checkbox.setChecked(current_settings.get('auto_delete_completed_posts', False))
        form_layout.addRow(self.auto_delete_checkbox)

        # 동시실행 갯수 설정
        self.concurrent_limit_input = QSpinBox()
        self.concurrent_limit_input.setRange(1, 50)
        self.concurrent_limit_input.setSuffix(" 개 (1-50)")
        self.concurrent_limit_input.setValue(current_settings.get('concurrent_limit', 1))
        form_layout.addRow("글쓰기 동시 실행 개수:", self.concurrent_limit_input)

        # --- 스하리 동시 실행 개수 추가 ---
        self.sahari_concurrent_limit_input = QSpinBox()
        self.sahari_concurrent_limit_input.setRange(1, 50)
        self.sahari_concurrent_limit_input.setSuffix(" 개 (1-50)")
        self.sahari_concurrent_limit_input.setValue(current_settings.get('sahari_concurrent_limit', 1))
        form_layout.addRow("스하리 동시 실행 개수:", self.sahari_concurrent_limit_input)

        # 크롬 경로 입력란 + 찾기/자동검색 버튼
        chrome_path_layout = QHBoxLayout()
        self.chrome_path_input = QLineEdit()
        self.chrome_path_input.setPlaceholderText("크롬 실행 파일 경로 (예: C:/Program Files/Google/Chrome/Application/chrome.exe)")
        self.chrome_path_input.setText(current_settings.get('chrome_path', ''))
        chrome_path_btn = QPushButton("찾기")
        chrome_path_btn.clicked.connect(self.find_chrome_path)
        chrome_path_btn.setFixedHeight(self.chrome_path_input.sizeHint().height())
        chrome_path_btn.setStyleSheet("font-size: 13px;")
        chrome_auto_btn = QPushButton("자동검색")
        chrome_auto_btn.clicked.connect(self.auto_find_chrome_path)
        chrome_auto_btn.setFixedHeight(self.chrome_path_input.sizeHint().height())
        chrome_auto_btn.setStyleSheet("font-size: 13px;")
        chrome_path_layout.addWidget(self.chrome_path_input)
        chrome_path_layout.addWidget(chrome_path_btn)
        chrome_path_layout.addWidget(chrome_auto_btn)
        form_layout.addRow("크롬 경로:", chrome_path_layout)

        layout.addWidget(form_group)

        # Buttons
        button_box = QHBoxLayout()
        ok_button = QPushButton("저장")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("취소")
        cancel_button.clicked.connect(self.reject)
        button_box.addStretch()
        button_box.addWidget(ok_button)
        button_box.addWidget(cancel_button)
        layout.addLayout(button_box)

    def find_chrome_path(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "크롬 실행 파일 선택", "", "실행 파일 (*.exe)")
        if file_path:
            self.chrome_path_input.setText(file_path)

    def auto_find_chrome_path(self):
        import os
        import ctypes
        from PyQt5.QtWidgets import QMessageBox
        candidates = [
            r"C:/Program Files/Google/Chrome/Application/chrome.exe",
            r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%/Google/Chrome/Application/chrome.exe"),
        ]
        for path in candidates:
            if os.path.exists(path):
                self.chrome_path_input.setText(path)
                QMessageBox.information(self, "크롬 경로 자동검색", f"크롬을 찾았습니다!\n{path}")
                return
        QMessageBox.warning(self, "크롬 경로 자동검색", "크롬 실행 파일을 찾을 수 없습니다. 수동으로 선택해 주세요.")

    def get_settings(self):
        """다이얼로그의 현재 설정 값을 딕셔너리로 반환"""
        return {
            'repeat_interval': self.repeat_interval_input.value(),
            'auto_delete_completed_posts': self.auto_delete_checkbox.isChecked(),
            'chrome_path': self.chrome_path_input.text().strip(),
            'concurrent_limit': self.concurrent_limit_input.value(),
            'sahari_concurrent_limit': self.sahari_concurrent_limit_input.value()
        }


class SahariWorker(QThread):
    """스하리 작업 스레드"""
    finished = pyqtSignal()
    progress = pyqtSignal(str)
    retry_needed = pyqtSignal(int)
    
    DELAY_JITTER = 0.5
    
    def __init__(self, search_query: str, delay_seconds: int, manual_comments: str, email: str, password: str, follow_count: int, like_range: str, repost_range: str, comment_range: str, proxy_server: str = '', proxy_username: str = '', proxy_password: str = '', start_index: int = 1):
        super().__init__()
        self.search_query = search_query
        self.delay_seconds = delay_seconds
        self.manual_comments = manual_comments
        self.email = email
        self.password = password
        self.follow_count = follow_count
        self.like_range = like_range
        self.repost_range = repost_range
        self.comment_range = comment_range
        self.proxy_server = proxy_server
        self.proxy_username = proxy_username
        self.proxy_password = proxy_password
        self.is_running = True
        # 오류 카운터
        self.username_error_count = 0
        self.next_error_count = 0
        self.max_error = 3
        self.retry_count = 0
        self.max_retry = 3
        self.start_index = start_index
    
    def apply_delay(self):
        """지연 시간 적용"""
        if not self.is_running:
            return
        delay_time = random.uniform(
            max(0.1, self.delay_seconds - self.DELAY_JITTER),
            self.delay_seconds + self.DELAY_JITTER
        )
        # QThread에서 안전한 대기
        for _ in range(int(delay_time * 10)):
            if not self.is_running:
                return
            time.sleep(0.1)
    
    def run_playwright(self, playwright: Playwright):
        """Playwright 자동화 실행"""
        session_path = get_session_dir(self.email, self.password)
        
        self.progress.emit(f'📁 세션 디렉토리: {session_path}')
        
        if not Path(session_path).exists():
            self.progress.emit(f'❌ Session path does not exist: {session_path}')
            return
        else:
            self.progress.emit(f'✅ 세션 디렉토리 존재 확인: {session_path}')
        
        if self.proxy_server:
            self.progress.emit(f'🌐 프록시 연결 시도: {self.proxy_server}')
        else:
            self.progress.emit('🌐 직접 연결로 브라우저 시작')
        
        try:
            context = launch_user_context(playwright, self.email, self.password, self.proxy_server, self.proxy_username, self.proxy_password)
            
            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()
            
            if self.proxy_server:
                self.progress.emit('✅ 프록시 연결 성공')
            
            self.progress.emit('로그인 상태 확인 중...')
            
            # Threads 메인 페이지로 이동
            page.goto('https://www.threads.com', timeout=15000)
            
            if is_login_required(page):
                self.progress.emit('로그인이 필요합니다. 자동 로그인을 시도합니다...')
                # 로그인 페이지로 이동
                page.goto('https://www.threads.com/login?hl=ko', timeout=15000)
                perform_login(page, self.email, self.password)
                self.progress.emit(f'🔐 자동 로그인 성공: {self.email}')
            else:
                self.progress.emit('✅ 이미 로그인되어 있습니다.')
            
            # 검색 페이지로 이동
            page.goto(f'https://www.threads.net/search?q={self.search_query}&serp_type=default')
            # 안전한 대기
            for _ in range(20):
                if not self.is_running:
                    break
                time.sleep(0.1)
            
            self.progress.emit('스크롤 완료, 요소 수집 중...')
            
            # XPath로 요소 수집
            elements = page.locator('xpath=//*[@id="barcelona-page-layout"]/div/div/div[2]/div[1]/div[1]/div/div[2]/div/div/div[1]/div')
            
            count = 1
            follow_done = 0
            
            # 범위 파싱
            like_min, like_max = parse_range(self.like_range)
            repost_min, repost_max = parse_range(self.repost_range)
            comment_min, comment_max = parse_range(self.comment_range)
            
            self.username_error_count = 0
            self.next_error_count = 0
            retry_this_thread = False
            while count <= self.follow_count and self.is_running:
                # 안전한 대기
                for _ in range(10):
                    if not self.is_running:
                        break
                    time.sleep(0.1)
                if not self.is_running:
                    break
                
                # 요소 스크롤 및 사용자명 추출 (간단하고 안전한 버전)
                try:
                    elements.nth(count - 1).scroll_into_view_if_needed()
                    page.wait_for_timeout(1000)  # 충분한 대기 시간
                    
                    # 사용자명 추출
                    link_elements = elements.nth(count - 1).get_by_role('link')
                    if link_elements.count() > 0:
                        username = link_elements.nth(0).all_inner_texts()[0].strip()
                        
                        # 사용자명 유효성 검사
                        if username and len(username) > 0 and not username.isspace():
                            self.progress.emit(f'사용자 처리 중: {username}')
                            self.username_error_count = 0  # 성공 시 오류 카운터 초기화
                        else:
                            self.progress.emit(f'사용자명이 비어있어 건너뜁니다. (count: {count})')
                            self.username_error_count += 1
                            if self.username_error_count >= self.max_error:
                                self.progress.emit(f'사용자명 추출 오류 {self.max_error}회 초과 - 다음 과정으로 이동')
                                count += 1
                                self.username_error_count = 0
                                self.next_error_count += 1
                                if self.next_error_count >= self.max_error:
                                    self.progress.emit(f'다음 과정에서도 오류 {self.max_error}회 초과 - 스하리 워커 재시작')
                                    retry_this_thread = True
                                    break
                            continue  # count 증가하지 않고 건너뛰기
                    else:
                        self.progress.emit(f'사용자 링크를 찾을 수 없어 건너뜁니다. (count: {count})')
                        self.username_error_count += 1
                        if self.username_error_count >= self.max_error:
                            self.progress.emit(f'사용자명 추출 오류 {self.max_error}회 초과 - 다음 과정으로 이동')
                            count += 1
                            self.username_error_count = 0
                            self.next_error_count += 1
                            if self.next_error_count >= self.max_error:
                                self.progress.emit(f'다음 과정에서도 오류 {self.max_error}회 초과 - 스하리 워커 재시작')
                                retry_this_thread = True
                                break
                        continue  # count 증가하지 않고 건너뛰기
                except Exception as e:
                    self.progress.emit(f'사용자명 추출 중 오류 발생: {e} - 건너뜁니다. (count: {count})')
                    self.username_error_count += 1
                    if self.username_error_count >= self.max_error:
                        self.progress.emit(f'사용자명 추출 오류 {self.max_error}회 초과 - 다음 과정으로 이동')
                        count += 1
                        self.username_error_count = 0
                        self.next_error_count += 1
                        if self.next_error_count >= self.max_error:
                            self.progress.emit(f'다음 과정에서도 오류 {self.max_error}회 초과 - 스하리 워커 재시작')
                            retry_this_thread = True
                            break
                    continue  # count 증가하지 않고 건너뛰기
                
                # 사용자 페이지에서 팔로우 및 게시물 작업
                user_page = context.new_page()
                user_page.goto(f'https://www.threads.com/@{username}')
                
                # 페이지 로딩 대기 및 검증
                try:
                    user_page.wait_for_load_state('networkidle', timeout=10000)  # 네트워크 대기
                    user_page.wait_for_timeout(2000)  # 추가 대기
                    
                    # 페이지가 제대로 로드되었는지 확인
                    current_url = user_page.url
                    if 'threads.com' not in current_url or 'error' in current_url.lower():
                        self.progress.emit(f'사용자 페이지 로딩 실패: {current_url} - 건너뜁니다.')
                        user_page.close()
                        continue  # count 증가하지 않고 건너뛰기
                        
                except Exception as e:
                    self.progress.emit(f'사용자 페이지 로딩 오류: {e} - 건너뜁니다.')
                    user_page.close()
                    continue  # count 증가하지 않고 건너뛰기
                
                # 팔로우 수행 (안정성 개선)
                try:
                    follow_button = user_page.get_by_role('button', name='팔로우').first
                    if follow_button.is_visible():
                        follow_button.click()
                        user_page.wait_for_timeout(100)  # 클릭 후 대기
                        follow_done += 1
                        self.progress.emit(f'팔로우 완료: {username} ({follow_done}/{self.follow_count})')
                        # 통계 업데이트 시그널 발생
                        if hasattr(self, 'stats_updated'):
                            self.stats_updated('팔로우')
                        self.apply_delay()
                    else:
                        self.progress.emit(f'팔로우 버튼을 찾을 수 없음: {username}')
                except Exception as e:
                    self.progress.emit(f'팔로우 수행 중 오류: {e}')
                
                # 사용자 페이지에서 게시물 찾기 (안정성 개선)
                try:
                    user_page.wait_for_timeout(100)  # 게시물 로딩 대기
                    user_posts = user_page.locator('div[data-pressable-container="true"]')
                    post_count = user_posts.count()
                    
                    self.progress.emit(f'사용자 {username}의 게시물 {post_count}개를 찾았습니다')
                except Exception as e:
                    self.progress.emit(f'게시물 수집 중 오류: {e}')
                    post_count = 0
                
                if post_count > 0:
                    # 해당 사용자의 게시물에만 작업 수행
                    
                    # 좋아요 수행
                    like_count_for_user = random.randint(like_min, like_max)
                    if like_count_for_user > 0:
                        self.progress.emit(f'좋아요 {like_count_for_user}개 수행 예정')
                        like_performed = 0
                        
                        for like_idx in range(min(like_count_for_user, post_count)):
                            if not self.is_running:
                                break
                            
                            try:
                                # 현재 사용자의 게시물에 좋아요
                                current_post = user_posts.nth(like_idx)
                                current_post.scroll_into_view_if_needed()
                                user_page.wait_for_timeout(1000)
                                
                                # 좋아요 상태 확인
                                if current_post.get_by_role('button').filter(has_text='좋아요 취소').is_visible():
                                    self.progress.emit(f'이미 좋아요되어 있음 - 건너뜀')
                                else:
                                    like_buttons = current_post.get_by_role('button').filter(has_text='좋아요')
                                    if like_buttons.count() == 1:
                                        like_buttons.first.click()
                                    else:
                                        like_buttons.nth(0).click()
                                    
                                    like_performed += 1
                                    self.progress.emit(f'좋아요 완료 ({like_performed}/{like_count_for_user})')
                                    # 통계 업데이트 시그널 발생
                                    if hasattr(self, 'stats_updated'):
                                        self.stats_updated('좋아요')
                                    self.apply_delay()
                            except Exception as e:
                                self.progress.emit(f'좋아요에 실패했습니다: {e}')
                        
                        if like_performed > 0:
                            self.progress.emit(f'총 {like_performed}개 좋아요 완료')
                    
                    # 리포스트 수행
                    repost_count_for_user = random.randint(repost_min, repost_max)
                    if repost_count_for_user > 0 and self.is_running:
                        self.progress.emit(f'리포스트 {repost_count_for_user}개 수행 예정')
                        repost_performed = 0
                        
                        for repost_idx in range(min(repost_count_for_user, post_count)):
                            if not self.is_running:
                                break
                            
                            try:
                                # 현재 사용자의 게시물에 리포스트
                                current_post = user_posts.nth(repost_idx)
                                current_post.scroll_into_view_if_needed()
                                user_page.wait_for_timeout(1000)
                                
                                repost_button = current_post.get_by_role('button').filter(has_text='리포스트').first
                                if repost_button.is_visible():
                                    repost_button.click()
                                    user_page.wait_for_timeout(100)
                                    user_page.get_by_role('button', name='리포스트 리포스트').click()
                                    repost_performed += 1
                                    self.progress.emit(f'리포스트 완료 ({repost_performed}/{repost_count_for_user})')
                                    # 통계 업데이트 시그널 발생
                                    if hasattr(self, 'stats_updated'):
                                        self.stats_updated('리포스트')
                                    self.apply_delay()
                                else:
                                    self.progress.emit(f'이미 리포스트되어 있음 - 건너뜀')
                            except Exception as e:
                                self.progress.emit(f'리포스트에 실패했습니다: {e}')
                        
                        if repost_performed > 0:
                            self.progress.emit(f'총 {repost_performed}개 리포스트 완료')
                    
                    # 댓글 작성
                    comment_count_for_user = random.randint(comment_min, comment_max)
                    if comment_count_for_user > 0 and self.is_running:
                        self.progress.emit(f'댓글 {comment_count_for_user}개 작성 예정')
                        comment_performed = 0
                        
                        for comment_idx in range(min(comment_count_for_user, post_count)):
                            if not self.is_running:
                                break
                            
                            comment = get_random_comment(self.manual_comments)
                            if not comment:
                                self.progress.emit('사용 가능한 랜덤 댓글이 없습니다. 댓글을 건너뜁니다.')
                                break
                            
                            try:
                                # 현재 사용자의 게시물에 댓글
                                current_post = user_posts.nth(comment_idx)
                                current_post.scroll_into_view_if_needed()
                                user_page.wait_for_timeout(1000)
                                
                                # 답글 버튼 클릭
                                reply_button = current_post.get_by_role('button').filter(has_text='답글').first
                                if reply_button.is_visible():
                                    reply_button.click()
                                    user_page.wait_for_timeout(500)
                                    
                                    # 텍스트박스 찾기 (인라인/모달 방식 모두 지원)
                                    inline_textbox = user_page.get_by_role('textbox', name='텍스트 필드가 비어 있습니다. 입력하여 새 게시물을 작성해보세요')
                                    modal_textbox = user_page.get_by_role('textbox', name='텍스트 필드가 비어 있습니다. 입력하여 새 게시물을 작성해보세요')
                                    
                                    # 인라인 방식 댓글 입력
                                    if inline_textbox.count() > 1:
                                        inline_textbox.nth(comment_idx).fill(comment)
                                        self.progress.emit(f"인라인 방식으로 댓글 입력: '{comment}'")
                                        
                                        if current_post.get_by_role('button').filter(has_text='답글').count() > 1:
                                            post_button = current_post.get_by_role('button', name='답글').nth(2)
                                        else:
                                            post_button = current_post.get_by_role('button', name='게시')
                                    else:
                                        # 모달 방식 댓글 입력
                                        modal_textbox.first.fill(comment)
                                        self.progress.emit(f"모달 방식으로 댓글 입력: '{comment}'")
                                        
                                        if user_page.get_by_role('button', name='답글').count() > 1:
                                            post_button = current_post.get_by_role('button', name='답글').nth(1)
                                        else:
                                            post_button = user_page.get_by_role('button', name='게시')
                                    
                                    post_button.click()
                                    # '게시되었습니다' 토스트 메시지가 나올 때까지 1초 단위로 반복 체크 (최대 30초)
                                    found_toast = False
                                    for _ in range(30):
                                        if not self.is_running:
                                            break
                                        if user_page.locator('text=게시되었습니다').is_visible():
                                            found_toast = True
                                            break
                                        user_page.wait_for_timeout(1000)
                                    if found_toast:
                                        comment_performed += 1
                                        self.progress.emit(f"댓글 완료 ({comment_performed}/{comment_count_for_user})")
                                        # 통계 업데이트 시그널 발생
                                        if hasattr(self, 'stats_updated'):
                                            self.stats_updated('댓글')
                                        self.apply_delay()
                                    else:
                                        self.progress.emit("❌ 댓글 토스트 미확인 - 댓글 실패로 간주(카운트 증가 없음)")
                                else:
                                    self.progress.emit(f'댓글 버튼을 찾을 수 없음 - 건너뜀')
                            except Exception as e:
                                self.progress.emit(f'댓글 작성에 실패했습니다: {e}')
                        
                        if comment_performed > 0:
                            self.progress.emit(f'총 {comment_performed}개 댓글 완료')
                else:
                    self.progress.emit(f'사용자 {username}의 게시물을 찾을 수 없습니다')
                
                # 사용자 페이지 닫기
                user_page.close()
                
                count += 1
                self.apply_delay()
            
            context.close()
            # 워커 재시작 로직
            if retry_this_thread and self.is_running:
                self.progress.emit(f'스하리 워커를 재시작합니다. (남은 작업: {self.follow_count - count + 1}개)')
                self.retry_needed.emit(count)
                return  # 반드시 return하여 finished.emit()이 호출되지 않도록!
        except Exception as e:
            if self.proxy_server:
                self.progress.emit(f'❌ 프록시 연결에 실패했습니다: {str(e)}')
            else:
                self.progress.emit(f'❌ 브라우저 시작에 실패했습니다: {str(e)}')
        except Exception as e:
            self.progress.emit(f'❌ 자동 로그인에 실패했습니다: {str(e)}')
            context.close()
    
    def run(self):
        """메인 작업 실행"""
        self.progress.emit('Playwright 자동화를 시작합니다...')
        
        try:
            with sync_playwright() as playwright:
                self.run_playwright(playwright)
        except Exception as e:
            self.progress.emit(f'❌ Playwright 실행에 실패했습니다: {str(e)}')
        finally:
            self.finished.emit()
    

    
    def stop(self):
        """작업 중지"""
        self.is_running = False
        self.progress.emit("스하리 작업 중지 신호를 받았습니다.")
        self.quit()
        self.wait()


class ParallelWorker(QThread):
    """순차적 작업을 처리하는 마스터 쓰레드"""
    log_updated = pyqtSignal(str)
    account_status_updated = pyqtSignal(int, str)
    post_status_updated = pyqtSignal(int, str)
    process_finished = pyqtSignal()
    # 안전한 메인 GUI 통신을 위한 시그널 추가
    save_posts_data = pyqtSignal(int, int)  # post_index, repeat_progress
    post_status_update = pyqtSignal(int, str)  # post_index, status

    def __init__(self, accounts, posts, settings, main_gui):
        super().__init__()
        self.checked_accounts = accounts  # 선택된 계정만 저장
        self.posts_to_process = posts
        self.settings = settings
        self.main_gui = main_gui  # 메인 GUI 객체 참조 (읽기 전용)
        self.is_running = True
        self.post_results = {}  # 게시물별 결과 저장 {post_index: [(url, timestamp), ...]}
        self._excel_lock = threading.Lock()  # 엑셀 저장 동시 접근 방지

    def run(self):
        try:
            concurrent_limit = self.settings.get('concurrent_limit', 1)
            
            if concurrent_limit == 1:
                # 기존 순차 처리
                self.sequential_processing()
            else:
                # 병렬 처리
                self.parallel_processing(concurrent_limit)
                
        except Exception as e:
            self.log_updated.emit(f"❌ 작업 중 심각한 오류 발생: {e}")
        finally:
            self.process_finished.emit()

    def sequential_processing(self):
        """기존 순차 처리 로직"""
        for i, (post_index, post_data) in enumerate(self.posts_to_process):
            if not self.is_running: break
            if self.settings.get('repeat_interval', 0) == 0:
                repeat_count = 1
            else:
                repeat_count = post_data.get('repeat_count', 1)
            repeat_progress = post_data.get('repeat_progress', 0)
            # --- 계정별 직전 결과 저장용 딕셔너리 추가 ---
            account_last_result = {}
            for acc_index, account_data in self.checked_accounts:
                account_last_result[acc_index] = self.main_gui.accounts[acc_index].get('status', '대기중')
            for rep in range(repeat_progress, repeat_count):
                if not self.is_running: break
                self.log_updated.emit(f"➡️ 게시글 '{post_data['title']}' 작업을 시작합니다. (반복 {rep+1}/{repeat_count})")
                self.post_status_updated.emit(post_index, f"{rep+1}/{repeat_count} 진행 중")
                post_successful_for_all_accounts = True
                for acc_index, account_data in self.checked_accounts:
                    if not self.is_running:
                        post_successful_for_all_accounts = False
                        break
                    success = self.process_single_account(acc_index, account_data, post_data, post_index)
                    # --- 계정별 결과 저장 ---
                    if success:
                        account_last_result[acc_index] = "완료"
                    else:
                        account_last_result[acc_index] = "실패"
                    if not success:
                        post_successful_for_all_accounts = False
                # 크로스 쓰레드 안전한 데이터 저장을 위해 시그널 사용
                self.save_posts_data.emit(post_index, rep+1)
                self.post_status_updated.emit(post_index, f"{rep+1}/{repeat_count} 완료")
                if not post_successful_for_all_accounts:
                    self.log_updated.emit(f"⚠️ 게시글 '{post_data['title']}' 작업 중 일부 계정에서 실패가 발생했습니다. (반복 {rep+1}/{repeat_count})")
                # --- 엑셀 저장: 반복마다 1개씩 저장 ---
                self.save_post_results_to_excel(post_index, post_data)
                # post_results 비우기 (반복마다 1개만 저장)
                if post_index in self.post_results:
                    del self.post_results[post_index]
                # 반복 간 대기 (마지막 반복이 아니고, 반복 간격이 0보다 크면)
                if rep < repeat_count - 1 and self.settings.get('repeat_interval', 0) > 0:
                    wait_seconds = self.settings.get('repeat_interval', 0) * 60
                    self.log_updated.emit(f"⏱️ 다음 반복까지 {self.settings.get('repeat_interval', 0)}분 대기합니다... (중지하려면 전체중지 클릭)")
                    # --- 반복 대기 시 계정별 상태를 직전 결과로 유지 ---
                    for acc_index, _ in self.checked_accounts:
                        self.account_status_updated.emit(acc_index, account_last_result[acc_index])
                    for _ in range(wait_seconds):
                        if not self.is_running: break
                        time.sleep(1)
            # 게시글 완료 상태를 안전하게 업데이트 (sequential_processing)
            if self.is_running:
                # 정상 완료된 경우에만 "완료" 상태로 변경
                self.post_status_update.emit(post_index, "완료")
                self.save_posts_data.emit(post_index, 0)  # repeat_progress를 0으로 리셋
                self.post_status_updated.emit(post_index, "완료")
            # 작업이 중지된 경우 현재 진행 상황 유지 (별도 처리 없음)
            self.handle_repeat_interval(i)

    def parallel_processing(self, limit):
        """병렬 처리 로직"""
        for i, (post_index, post_data) in enumerate(self.posts_to_process):
            if not self.is_running: break
            if self.settings.get('repeat_interval', 0) == 0:
                repeat_count = 1
            else:
                repeat_count = post_data.get('repeat_count', 1)
            repeat_progress = post_data.get('repeat_progress', 0)
            # --- 계정별 직전 결과 저장용 딕셔너리 추가 ---
            account_last_result = {}
            for acc_index, account_data in self.checked_accounts:
                account_last_result[acc_index] = self.main_gui.accounts[acc_index].get('status', '대기중')
            for rep in range(repeat_progress, repeat_count):
                if not self.is_running: break
                self.log_updated.emit(f"➡️ 게시글 '{post_data['title']}' 병렬 작업을 시작합니다. (반복 {rep+1}/{repeat_count}) (동시실행: {limit}개)")
                self.post_status_updated.emit(post_index, f"{rep+1}/{repeat_count} 진행 중")
                account_groups = [self.checked_accounts[j:j+limit] for j in range(0, len(self.checked_accounts), limit)]
                post_successful_for_all_accounts = True
                for group_index, account_group in enumerate(account_groups):
                    if not self.is_running: break
                    self.log_updated.emit(f"   🔄 그룹 {group_index + 1}/{len(account_groups)} 병렬 처리 중... ({len(account_group)}개 계정)")
                    with ThreadPoolExecutor(max_workers=limit) as executor:
                        futures = []
                        for acc_index, account_data in account_group:
                            future = executor.submit(self.process_single_account, acc_index, account_data, post_data, post_index)
                            futures.append((acc_index, future))
                        for acc_index, future in futures:
                            try:
                                success = future.result()
                                # --- 계정별 결과 저장 ---
                                if success:
                                    account_last_result[acc_index] = "완료"
                                else:
                                    account_last_result[acc_index] = "실패"
                                if not success:
                                    post_successful_for_all_accounts = False
                            except Exception as e:
                                self.log_updated.emit(f"   ❌ 병렬 작업 중 오류: {e}")
                                account_last_result[acc_index] = "실패"
                                post_successful_for_all_accounts = False
                # 크로스 쓰레드 안전한 데이터 저장을 위해 시그널 사용
                self.save_posts_data.emit(post_index, rep+1)
                self.post_status_updated.emit(post_index, f"{rep+1}/{repeat_count} 완료")
                if not post_successful_for_all_accounts:
                    self.log_updated.emit(f"⚠️ 게시글 '{post_data['title']}' 작업 중 일부 계정에서 실패가 발생했습니다. (반복 {rep+1}/{repeat_count})")
                # --- 엑셀 저장: 반복마다 1개씩 저장 ---
                self.save_post_results_to_excel(post_index, post_data)
                # post_results 비우기 (반복마다 1개만 저장)
                if post_index in self.post_results:
                    del self.post_results[post_index]
                # 반복 간 대기 (마지막 반복이 아니고, 반복 간격이 0보다 크면)
                if rep < repeat_count - 1 and self.settings.get('repeat_interval', 0) > 0:
                    wait_seconds = self.settings.get('repeat_interval', 0) * 60
                    self.log_updated.emit(f"⏱️ 다음 반복까지 {self.settings.get('repeat_interval', 0)}분 대기합니다... (중지하려면 전체중지 클릭)")
                    # --- 반복 대기 시 계정별 상태를 직전 결과로 유지 ---
                    for acc_index, _ in self.checked_accounts:
                        self.account_status_updated.emit(acc_index, account_last_result[acc_index])
                    for _ in range(wait_seconds):
                        if not self.is_running: break
                        time.sleep(1)
            # 게시글 완료 상태를 안전하게 업데이트 (parallel_processing)
            if self.is_running:
                # 정상 완료된 경우에만 "완료" 상태로 변경
                self.post_status_update.emit(post_index, "완료")
                self.save_posts_data.emit(post_index, 0)  # repeat_progress를 0으로 리셋
                self.post_status_updated.emit(post_index, "완료")
            # 작업이 중지된 경우 현재 진행 상황 유지 (별도 처리 없음)
            self.handle_repeat_interval(i)

    def process_single_account(self, acc_index, account_data, post_data, post_index):
        """단일 계정 처리 로직"""
        try:
            self.log_updated.emit(f"   - 계정 '{account_data['username']}'으로 작업 시작...")
            self.account_status_updated.emit(acc_index, f"'{post_data['title']}' 작업 중")

            api_id = account_data.get('api_id')
            token = account_data.get('token')
            proxy_ip = account_data.get('proxy_ip')
            proxy_port = account_data.get('proxy_port')

            if not api_id or not token:
                raise ValueError("API ID와 토큰이 설정되지 않았습니다.")

            proxies = None
            # 프록시 입력이 모두 공백이 아니고, 값이 있을 때만 프록시 적용
            if proxy_ip and proxy_port and proxy_ip.strip() and proxy_port.strip():
                proxy_url = f"http://{proxy_ip}:{proxy_port}"
                proxies = {"http": proxy_url, "https": proxy_url}
                self.log_updated.emit(f"     - 프록시({proxy_ip}:{proxy_port}) 연결 확인 중...")
                actual_ip = threads_api.check_proxy_ip(proxies=proxies)
                if actual_ip == proxy_ip:
                    self.log_updated.emit(f"     ✅ 정상적으로 IP 설정({actual_ip})이 완료 되었습니다. 글쓰기 등록을 시작합니다.")
                else:
                    # 팝업 없이 로그만 남기고 실패 처리
                    self.log_updated.emit(f"     - [{account_data['username']}] 프록시 오류로 접속 실패")
                    return False
            else:
                self.log_updated.emit("     - 프록시 설정 없음. 직접 연결합니다.")

            post_type = post_data.get('post_type', 'regular')
            content = post_data.get('content', '')

            if post_type == 'slide':
                media_items = post_data.get('media_items', [])
                if not media_items:
                    raise ValueError("슬라이드 게시물에 미디어 아이템이 없습니다.")
                
                self.log_updated.emit(f"     - 슬라이드(미디어 {len(media_items)}개) 게시를 시도합니다.")
                success, result = carousel_api.post_carousel(api_id, token, media_items, content, proxies=proxies)
                if not success:
                    raise Exception(result)

            else: # 'regular' post type
                image_url_str = post_data.get('image_url', '').strip()
                video_url = post_data.get('video_url', '').strip()

                if video_url:
                    self.log_updated.emit(f"     - 동영상 게시를 시도합니다: {video_url}")
                    success, result = threads_api.post_video(api_id, token, video_url, content, proxies=proxies)
                    if not success:
                        raise Exception(result)
                    # 동영상 API 호출 후 30초 대기
                    for _ in range(300):
                        if not self.is_running:
                            break
                        time.sleep(0.1)
                
                elif image_url_str:
                    image_urls = [url.strip() for url in image_url_str.split(',') if url.strip()]
                    if len(image_urls) > 1:
                        self.log_updated.emit(f"     - 캐러셀(이미지 {len(image_urls)}개) 게시를 시도합니다.")
                        success, result = threads_api.post_carousel(api_id, token, image_urls, content, proxies=proxies)
                        if not success:
                            raise Exception(result)
                    elif len(image_urls) == 1:
                        self.log_updated.emit(f"     - 단일 이미지 게시를 시도합니다: {image_urls[0]}")
                        success, result = threads_api.post_single_image(api_id, token, image_urls[0], content, proxies=proxies)
                        if not success:
                            raise Exception(result)
                    else: # 이미지 URL이 비어있는 경우
                        self.log_updated.emit("     - 텍스트만 게시합니다.")
                        success, result = threads_api.post_text(api_id, token, content, proxies=proxies)
                        if not success:
                            raise Exception(result)
                
                else:
                    self.log_updated.emit("     - 텍스트만 게시합니다.")
                    success, result = threads_api.post_text(api_id, token, content, proxies=proxies)
                    if not success:
                        raise Exception(result)

            self.log_updated.emit(f"   ✔️ 계정 '{account_data['username']}' 작업 성공!")

            # 게시글 URL 로그 추가 - API permalink 사용
            if 'result' in locals() and result:
                post_id = result.get('id')
                if post_id:
                    permalink_url = None
                    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
                    
                    try:
                        import requests
                        post_info_url = f"https://graph.threads.net/v1.0/{post_id}"
                        post_params = {
                            "fields": "permalink",
                            "access_token": token
                        }
                        post_response = requests.get(post_info_url, params=post_params, proxies=proxies, timeout=10)
                        if post_response.status_code == 200:
                            post_info = post_response.json()
                            permalink = post_info.get('permalink')
                            if permalink:
                                permalink_url = permalink
                                self.log_updated.emit(f"      게시글 URL: {permalink}")
                            else:
                                # 대체 URL 형태
                                permalink_url = f"https://www.threads.com/t/{post_id}"
                                self.log_updated.emit(f"      게시글 URL: {permalink_url}")
                        else:
                            # API 조회 실패 시 대체 URL 형태
                            permalink_url = f"https://www.threads.com/t/{post_id}"
                            self.log_updated.emit(f"      게시글 URL: {permalink_url}")
                    except Exception as e:
                        # 오류 발생 시 대체 URL 형태
                        permalink_url = f"https://www.threads.com/t/{post_id}"
                        self.log_updated.emit(f"      게시글 URL: {permalink_url}")
                    
                    # 엑셀 저장용 데이터 수집 (계정별로 모두 누적)
                    if permalink_url:
                        if post_index not in self.post_results:
                            self.post_results[post_index] = []
                        self.post_results[post_index].append((permalink_url, current_time))

            self.account_status_updated.emit(acc_index, "완료")
            return True

        except Exception as e:
            error_message = f"   ❌ 계정 '{account_data['username']}' 작업 실패: {e}"
            self.log_updated.emit(error_message)
            self.account_status_updated.emit(acc_index, "실패")
            return False

    def handle_repeat_interval(self, current_post_index):
        """반복 설정에 따른 대기 처리"""
        is_last_post = (current_post_index == len(self.posts_to_process) - 1)
        repeat_interval_minutes = self.settings.get('repeat_interval', 0)
        
        if not is_last_post and repeat_interval_minutes > 0:
            wait_seconds = repeat_interval_minutes * 60
            self.log_updated.emit(f"⏱️ 다음 작업까지 {repeat_interval_minutes}분 대기합니다... (중지하려면 전체중지 클릭)")
            for _ in range(wait_seconds):
                if not self.is_running: break
                time.sleep(1)

    def save_post_results_to_excel(self, post_index, post_data):
        """게시물별 엑셀 파일 저장 (멀티쓰레드 안전)"""
        with self._excel_lock:  # 엑셀 저장 동시 접근 방지
            try:
                if post_index not in self.post_results or not self.post_results[post_index]:
                    self.log_updated.emit(f"⚠️ '{post_data['title']}' 게시물의 URL 데이터가 없어 엑셀 저장을 생략합니다.")
                    return

                # data 폴더 생성 (없으면)
                data_folder = "data"
                if not os.path.exists(data_folder):
                    try:
                        os.makedirs(data_folder, exist_ok=True)
                        self.log_updated.emit(f"📁 '{data_folder}' 폴더를 생성했습니다.")
                    except OSError as e:
                        self.log_updated.emit(f"❌ 폴더 생성 실패: {e}")
                        return

                # 파일명 생성 (특수문자 제거 및 중복 방지)
                import re
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', post_data['title'])  # 파일명 특수문자 처리
                safe_title = safe_title.strip()[:50]  # 길이 제한
                if not safe_title:
                    safe_title = f"게시물_{post_index}"
                
                # 중복 파일명 처리 및 파일 락 검증
                base_filename = f"{safe_title}.xlsx"
                filename = os.path.join(data_folder, base_filename)
                counter = 1
                max_attempts = 100  # 무한 루프 방지
                while counter <= max_attempts:
                    if not os.path.exists(filename):
                        break
                    filename = os.path.join(data_folder, f"{safe_title}_{counter}.xlsx")
                    counter += 1
                
                if counter > max_attempts:
                    self.log_updated.emit(f"❌ 파일명 생성 실패: 너무 많은 중복 파일 ({max_attempts}개 이상)")
                    return
                
                # 엑셀 데이터 생성
                import pandas as pd
                
                data = []
                post_results_copy = list(self.post_results[post_index])  # 복사본 생성
                for url, timestamp in post_results_copy:
                    data.append([url, timestamp])
                
                if not data:
                    self.log_updated.emit(f"⚠️ '{post_data['title']}' 게시물의 데이터가 비어있어 저장을 건너뜁니다.")
                    return
                
                df = pd.DataFrame(data, columns=['URL', '등록시간'])
                
                # 엑셀 파일 저장 (재시도 로직 추가)
                max_save_attempts = 3
                for attempt in range(max_save_attempts):
                    try:
                        df.to_excel(filename, index=False, engine='openpyxl')
                        break
                    except PermissionError as e:
                        if attempt < max_save_attempts - 1:
                            self.log_updated.emit(f"⚠️ 파일 저장 재시도 중... ({attempt + 1}/{max_save_attempts})")
                            time.sleep(1)  # 1초 대기 후 재시도
                        else:
                            raise e
                    except Exception as e:
                        self.log_updated.emit(f"❌ 엑셀 저장 중 오류: {e}")
                        return
                
                # 결과 로그
                saved_count = len(data)
                self.log_updated.emit(f"📊 '{post_data['title']}' 게시물 결과를 엑셀로 저장했습니다:")
                self.log_updated.emit(f"   저장 위치: {data_folder}/")
                self.log_updated.emit(f"   파일명: {os.path.basename(filename)}")
                self.log_updated.emit(f"   저장 건수: {saved_count}개 URL")
                
                # 저장된 데이터 정리
                if post_index in self.post_results:
                    del self.post_results[post_index]
                    
            except Exception as e:
                self.log_updated.emit(f"❌ '{post_data['title']}' 게시물 엑셀 저장 실패: {e}")
                import traceback
                self.log_updated.emit(f"상세 오류: {traceback.format_exc()}")

    def stop(self):
        self.is_running = False
        self.log_updated.emit("... 작업 중지 신호를 받았습니다. 현재 단계를 완료 후 종료합니다.")


class MultiAccountGUI(QMainWindow):
    """50개 계정 관리 GUI"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🚀 무한 쓰레드 프로그램 v2.8 (스하리 기능 포함)")
        
        # 창 크기와 위치 설정
        self.load_window_geometry()
        
        # 데이터 초기화
        self.accounts = []
        self.posts = []
        self.is_running = False
        self.editing_post_index = None # 현재 수정 중인 게시글 인덱스
        self.settings = {}
        self.worker = None
        
        # 스하리 관련 변수들
        self.sahari_workers = {}  # 계정별 스하리 워커 저장
        self.sahari_is_running = False
        self.sahari_config = {}
        self.sahari_stats = {}  # 계정별 스하리 누적 통계 저장

        # UI 설정
        self.setup_ui()
        self.setup_styles()

        self.load_settings()
        self.accounts = self.load_data_from_file('accounts.json', self.generate_sample_accounts)
        self.posts = self.load_data_from_file('posts.json', self.generate_sample_posts)
        
        # 스하리 설정 및 통계 로드
        self.load_sahari_config()
        self.load_sahari_stats()
        
        self.account_table.setRowCount(len(self.accounts))
        self.load_account_data()
        self.post_table.setRowCount(len(self.posts))
        self.load_post_data()
        
        # 타이머 설정 (상태 업데이트용)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_statistics)
        self.timer.start(1000)  # 1초마다 업데이트
        
        # 창 크기 변경 이벤트 연결
        self.resizeEvent = self.on_resize_event

    def load_window_geometry(self):
        """저장된 창 크기와 위치를 불러옵니다."""
        try:
            if os.path.exists('window_geometry.json'):
                with open('window_geometry.json', 'r', encoding='utf-8') as f:
                    geometry = json.load(f)
                    
                x = geometry.get('x', 50)
                y = geometry.get('y', 50)
                width = geometry.get('width', 1600)
                height = geometry.get('height', 900)
                
                # 화면 크기 확인
                screen = QApplication.primaryScreen()
                screen_geometry = screen.geometry()
                screen_width = screen_geometry.width()
                screen_height = screen_geometry.height()
                
                # 창이 화면 밖으로 나가지 않도록 조정
                x = max(0, min(x, screen_width - width))
                y = max(0, min(y, screen_height - height))
                
                self.setGeometry(x, y, width, height)
            else:
                # 기본값으로 화면 중앙에 배치
                screen = QApplication.primaryScreen()
                screen_geometry = screen.geometry()
                screen_width = screen_geometry.width()
                screen_height = screen_geometry.height()
                
                # 화면 크기의 80%로 창 크기 설정 (최소 1200x700, 최대 1600x900)
                window_width = max(1200, min(1600, int(screen_width * 0.8)))
                window_height = max(700, min(900, int(screen_height * 0.8)))
                
                # 창을 화면 중앙에 배치
                x = (screen_width - window_width) // 2
                y = (screen_height - window_height) // 2
                
                self.setGeometry(x, y, window_width, window_height)
                
        except Exception as e:
            # 오류 발생 시 기본값 사용
            self.setGeometry(50, 50, 1600, 900)
    
    def save_window_geometry(self):
        """현재 창 크기와 위치를 저장합니다."""
        try:
            geometry = {
                'x': self.x(),
                'y': self.y(),
                'width': self.width(),
                'height': self.height()
            }
            with open('window_geometry.json', 'w', encoding='utf-8') as f:
                json.dump(geometry, f, indent=4, ensure_ascii=False)
        except Exception as e:
            # 저장 실패 시 조용히 처리
            pass
    
    def on_resize_event(self, event):
        """창 크기 변경 시 텍스트 영역 높이 조정"""
        super().resizeEvent(event)
        
        # 일반 게시글 내용 영역 높이 조정
        if hasattr(self, 'post_content_input'):
            content_height = max(300, self.height() // 2)
            self.post_content_input.setMinimumHeight(content_height)
            self.post_content_input.setMaximumHeight(content_height)
        
        # 슬라이드 게시글 내용 영역 높이 조정
        if hasattr(self, 'slide_post_content_input'):
            slide_content_height = max(200, self.height() // 3)
            self.slide_post_content_input.setMinimumHeight(slide_content_height)
            self.slide_post_content_input.setMaximumHeight(slide_content_height)
        
        # 슬라이드 미디어 영역 높이 조정
        if hasattr(self, 'slide_media_input'):
            media_height = max(150, self.height() // 4)
            self.slide_media_input.setMinimumHeight(media_height)
            self.slide_media_input.setMaximumHeight(media_height)
        
        # 버튼 크기도 조정
        if hasattr(self, 'start_all_btn'):
            button_width = max(150, self.width() // 6)
            self.start_all_btn.setFixedSize(button_width, 50)
            self.stop_all_btn.setFixedSize(button_width, 50)
            self.settings_btn.setFixedSize(button_width, 50)
            self.token_btn.setFixedSize(button_width, 50)

    def update_account_status(self, index, status_text):
        """계정 상태 UI 업데이트 (인덱스 범위 검증 강화)"""
        try:
            if not (0 <= index < len(self.accounts)):
                self.add_log(f"⚠️ 잘못된 계정 인덱스: {index} (최대: {len(self.accounts)-1})")
                return
            if not hasattr(self, 'account_table') or self.account_table is None:
                return
            self.accounts[index]['status'] = status_text
            # 상태 QLabel 업데이트
            status_widget = self.account_table.cellWidget(index, 7)
            if status_widget:
                label = status_widget.findChild(QLabel)
                if label:
                    label.setText(status_text)
                    label.setAlignment(Qt.AlignCenter)
                    if status_text == "완료":
                        label.setStyleSheet("background-color: rgb(200,255,200);")
                    elif status_text == "실패":
                        label.setStyleSheet("background-color: rgb(255,200,200);")
                    elif "진행" in status_text or "작업 중" in status_text:
                        label.setStyleSheet("background-color: rgb(255,255,200);")
                    else:
                        label.setStyleSheet("")
        except Exception as e:
            self.add_log(f"❌ 계정 상태 업데이트 중 오류: {e}")

    def load_data_from_file(self, filename, sample_generator):
        """JSON 파일에서 데이터를 불러옵니다. 파일이 없으면 샘플 데이터로 새로 생성합니다."""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.add_log(f"📋 저장된 데이터({filename})를 불러왔습니다.")
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            self.add_log(f"⚠️ {filename} 파일이 없거나 손상되어 새로 생성합니다.")
            data = sample_generator()
            self.save_data_to_file(filename, data)
            return data

    def save_data_to_file(self, filename, data):
        """현재 데이터를 JSON 파일에 저장합니다 (파일 락 안전)."""
        import tempfile
        import shutil
        
        try:
            # 임시 파일에 먼저 저장
            temp_filename = filename + '.tmp'
            with open(temp_filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            
            # 원자적 이동 (Windows에서도 안전)
            try:
                if os.path.exists(filename):
                    backup_filename = filename + '.bak'
                    shutil.move(filename, backup_filename)
                shutil.move(temp_filename, filename)
                # 백업 파일 정리
                if os.path.exists(backup_filename):
                    os.remove(backup_filename)
            except Exception:
                # 이동 실패 시 백업에서 복구
                if os.path.exists(backup_filename):
                    shutil.move(backup_filename, filename)
                raise
                
        except Exception as e:
            # 임시 파일 정리
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except:
                    pass
            if hasattr(self, 'add_log'):
                self.add_log(f"❌ {filename} 저장 실패: {e}")
            else:
                print(f"❌ {filename} 저장 실패: {e}")

    def generate_sample_accounts(self):
        """샘플 계정 생성 (초기 생성용)"""
        return [{'checked': True, 'username': f'user{i+1:02d}', 'password': f'pass{i+1:02d}', 'api_id': '', 'token': '', 'proxy_ip': '', 'proxy_port': '', 'status': '대기중'} for i in range(50)]
    
    def generate_sample_posts(self):
        """샘플 게시글 생성 (초기 생성용)"""
        return [
             {'title': '샘플 주제 1', 'content': '샘플 내용입니다.', 'image_url': '', 'video_url': '', 'status': '대기중', 'repeat_count': 1, 'repeat_progress': 0},
             {'title': '샘플 주제 2', 'content': '두 번째 샘플 내용입니다.', 'image_url': 'http://example.com/image.jpg', 'video_url': 'http://example.com/video.mp4', 'status': '대기중', 'repeat_count': 1, 'repeat_progress': 0}
        ]

    def setup_ui(self):
        """UI 구성"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(5, 5, 5, 5)

        self.setup_control_panel(main_layout)
        
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        self.setup_left_panel(splitter)
        self.setup_right_panel(splitter)
        
        # 화면 크기에 따라 분할 비율 조정
        total_width = self.width()
        left_width = int(total_width * 0.65)  # 왼쪽 65%
        right_width = total_width - left_width  # 오른쪽 35%
        splitter.setSizes([left_width, right_width])
        self.setup_status_bar(main_layout)

    def setup_control_panel(self, parent_layout):
        """상단 제어 패널"""
        control_group = QGroupBox("🎯 전체 제어")
        control_group.setFixedHeight(120)  # 버튼 영역을 항상 고정된 높이로 유지
        parent_layout.addWidget(control_group)
        control_layout = QVBoxLayout(control_group)
        control_layout.setSpacing(5)
        control_layout.setContentsMargins(10, 10, 10, 10)
        # title_label = QLabel("🚀 무한 쓰레드")
        # title_font = QFont("맑은 고딕", 12, QFont.Bold)
        # title_label.setFont(title_font)
        # title_label.setAlignment(Qt.AlignCenter)
        # control_layout.addWidget(title_label)
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        # 버튼 크기를 칸에 맞춰 설정
        button_width = max(150, self.width() // 6)  # 최소 150px, 화면 너비의 1/6
        button_height = 50  # 고정 높이
        self.start_all_btn = QPushButton("🚀 전체시작")
        self.start_all_btn.setFixedSize(button_width, button_height)
        self.start_all_btn.clicked.connect(self.start_all_accounts)
        button_layout.addWidget(self.start_all_btn)
        self.stop_all_btn = QPushButton("⏹️ 전체중지")
        self.stop_all_btn.setFixedSize(button_width, button_height)
        self.stop_all_btn.setEnabled(False)
        self.stop_all_btn.clicked.connect(self.stop_all_accounts)
        button_layout.addWidget(self.stop_all_btn)
        # --- 스하리중지 버튼 추가 ---
        self.sahari_stop_btn = QPushButton("⏹️ 스하리중지")
        self.sahari_stop_btn.setFixedSize(button_width, button_height)
        self.sahari_stop_btn.setEnabled(True)
        self.sahari_stop_btn.clicked.connect(self.stop_all_sahari_force)
        button_layout.addWidget(self.sahari_stop_btn)
        self.settings_btn = QPushButton("⚙️ 설정")
        self.settings_btn.setFixedSize(button_width, button_height)
        self.settings_btn.clicked.connect(self.open_settings)
        button_layout.addWidget(self.settings_btn)
        self.token_btn = QPushButton("🔑 토큰 발행")
        self.token_btn.setFixedSize(button_width, button_height)
        self.token_btn.clicked.connect(self.open_token_gui)
        button_layout.addWidget(self.token_btn)
        button_layout.addStretch()
        control_layout.addLayout(button_layout)

    def setup_left_panel(self, parent_splitter):
        """왼쪽 탭 패널 (계정, 게시글)"""
        left_widget = QWidget()
        layout = QVBoxLayout(left_widget)
        
        self.left_tabs = QTabWidget()
        layout.addWidget(self.left_tabs)

        # 계정 목록 탭
        account_tab = QWidget()
        self.setup_account_tab(account_tab)
        self.left_tabs.addTab(account_tab, "📋 계정 목록")

        # 게시글 관리 탭
        post_tab = QWidget()
        self.setup_post_tab(post_tab)
        self.left_tabs.addTab(post_tab, "📝 게시글 관리")

        # 스하리 관리 탭
        sahari_tab = QWidget()
        self.setup_sahari_tab(sahari_tab)
        self.left_tabs.addTab(sahari_tab, "🎯 스하리 관리")

        parent_splitter.addWidget(left_widget)

    def setup_account_tab(self, parent_tab):
        """계정 목록 탭 UI 구성"""
        layout = QVBoxLayout(parent_tab)
        
        select_all_layout = QHBoxLayout()
        select_all_layout.setSpacing(5)
        
        # 버튼 크기를 칸에 맞춰 설정
        button_height = 35  # 고정 높이
        
        self.select_all_accounts_btn = QPushButton("전체 선택")
        self.select_all_accounts_btn.setFixedHeight(button_height)
        self.select_all_accounts_btn.clicked.connect(self.toggle_select_all_accounts)
        select_all_layout.addWidget(self.select_all_accounts_btn)
        
        # --- 추가: 비밀번호 보이기/숨기기 버튼 ---
        self.password_visible = False
        self.toggle_password_btn = QPushButton("비밀번호 보이기")
        self.toggle_password_btn.setFixedHeight(button_height)
        self.toggle_password_btn.clicked.connect(self.toggle_password_visibility)
        select_all_layout.addWidget(self.toggle_password_btn)
        
        # --- 추가: 파일로 등록 버튼 ---
        self.import_accounts_btn = QPushButton("파일로 등록")
        self.import_accounts_btn.setFixedHeight(button_height)
        self.import_accounts_btn.clicked.connect(self.import_accounts_from_excel)
        select_all_layout.addWidget(self.import_accounts_btn)
        
        # --- 추가: 엑셀로 저장 버튼 ---
        self.export_accounts_btn = QPushButton("엑셀로 저장")
        self.export_accounts_btn.setFixedHeight(button_height)
        self.export_accounts_btn.clicked.connect(self.export_accounts_to_excel)
        select_all_layout.addWidget(self.export_accounts_btn)
        
        # --- 추가: 선택계정 삭제 버튼 ---
        self.delete_selected_accounts_btn = QPushButton("선택계정 삭제")
        self.delete_selected_accounts_btn.setFixedHeight(button_height)
        self.delete_selected_accounts_btn.clicked.connect(self.delete_selected_accounts)
        select_all_layout.addWidget(self.delete_selected_accounts_btn)
        
        # --- 추가: 자동 ID 반환 버튼 ---
        self.auto_get_api_id_btn = QPushButton("자동 ID 반환")
        self.auto_get_api_id_btn.setFixedHeight(button_height)
        self.auto_get_api_id_btn.clicked.connect(self.auto_get_api_id_from_token)
        select_all_layout.addWidget(self.auto_get_api_id_btn)
        # ---
        select_all_layout.addStretch()
        layout.addLayout(select_all_layout)
        
        self.account_table = QTableWidget(0, 10)
        self.account_table.setHorizontalHeaderLabels(["선택", "아이디", "비밀번호", "API ID", "토큰", "프록시 IP", "포트", "상태", "런처", "스하리"])
        header = self.account_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 선택
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        self.account_table.setColumnWidth(1, 80)           # 아이디
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        self.account_table.setColumnWidth(2, 80)           # 비밀번호
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        self.account_table.setColumnWidth(3, 120)          # API ID
        header.setSectionResizeMode(4, QHeaderView.Stretch)           # 토큰 (반응형)
        header.setSectionResizeMode(5, QHeaderView.Interactive)
        self.account_table.setColumnWidth(5, 100)          # 프록시 IP
        header.setSectionResizeMode(6, QHeaderView.Interactive)
        self.account_table.setColumnWidth(6, 50)           # 포트
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 상태
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)  # 런처
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)  # 스하리
        layout.addWidget(self.account_table)
        
    def setup_post_tab(self, parent_tab):
        """게시글 관리 탭 UI 구성"""
        layout = QVBoxLayout(parent_tab)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(5)
        
        # 버튼 크기를 칸에 맞춰 설정
        button_height = 35  # 고정 높이
        
        self.select_all_posts_btn = QPushButton("전체 선택")
        self.select_all_posts_btn.setFixedHeight(button_height)
        self.select_all_posts_btn.clicked.connect(self.toggle_select_all_posts)
        button_layout.addWidget(self.select_all_posts_btn)
      

        # --- 선택삭제 버튼 추가 ---
        self.delete_selected_btn = QPushButton("선택삭제")
        self.delete_selected_btn.setFixedHeight(button_height)
        self.delete_selected_btn.clicked.connect(self.delete_selected_posts)
        button_layout.addWidget(self.delete_selected_btn)
        
        # --- 상태초기화 버튼 추가 ---
        self.reset_status_btn = QPushButton("상태초기화")
        self.reset_status_btn.setFixedHeight(button_height)
        self.reset_status_btn.clicked.connect(self.reset_selected_status)
        button_layout.addWidget(self.reset_status_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        # --- 게시글 테이블: 체크박스 열 추가 (전체선택 체크박스 포함) ---
        self.post_table = QTableWidget(0, 10)
        self.post_table.setHorizontalHeaderLabels(["", "번호", "주제", "내용", "이미지URL", "동영상URL", "상태", "수정", "삭제", "반복"])
        header = self.post_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 체크박스
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 번호
        for i in [2, 3, 4, 5]: header.setSectionResizeMode(i, QHeaderView.Stretch)  # 주제, 내용, 이미지URL, 동영상URL
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 상태
        header.setSectionResizeMode(7, QHeaderView.Interactive)       # 수정 버튼
        self.post_table.setColumnWidth(7, 60)
        header.setSectionResizeMode(8, QHeaderView.Interactive)       # 삭제 버튼
        self.post_table.setColumnWidth(8, 60)
        header.setSectionResizeMode(9, QHeaderView.Interactive)       # 반복 버튼
        self.post_table.setColumnWidth(9, 60)
        layout.addWidget(self.post_table)

        # --- 전체선택 체크박스 구현 ---
        self.post_select_all_checkbox = QCheckBox()
        self.post_select_all_checkbox.stateChanged.connect(self.toggle_all_post_checkboxes)
        self.post_table.setCellWidget(0, 0, self.post_select_all_checkbox)

    def setup_sahari_tab(self, parent_tab):
        """스하리 관리 탭 UI 구성"""
        layout = QVBoxLayout(parent_tab)
        # 스하리 설정 그룹
        settings_group = QGroupBox("⚙️ 스하리 설정")
        settings_layout = QFormLayout(settings_group)
        # 검색어 입력
        self.sahari_search_input = QLineEdit()
        self.sahari_search_input.setPlaceholderText("검색할 키워드를 입력하세요! 예)스하리")
        self.sahari_search_input.textChanged.connect(self.auto_save_sahari_config)
        settings_layout.addRow("검색어:", self.sahari_search_input)
        # --- 지연시간 범위 입력 ---
        delay_range_layout = QHBoxLayout()
        delay_range_layout.setSpacing(4)
        self.sahari_delay_min_input = QSpinBox()
        self.sahari_delay_min_input.setRange(1, 60)
        self.sahari_delay_min_input.setValue(1)
        self.sahari_delay_min_input.setSuffix(" 초")
        self.sahari_delay_min_input.setFixedWidth(60)
        self.sahari_delay_min_input.valueChanged.connect(self.auto_save_sahari_config)
        delay_range_layout.addWidget(self.sahari_delay_min_input)
        delay_wave = QLabel("~")
        delay_wave.setContentsMargins(0,0,0,0)
        delay_wave.setFixedWidth(12)
        delay_range_layout.addWidget(delay_wave)
        self.sahari_delay_max_input = QSpinBox()
        self.sahari_delay_max_input.setRange(1, 60)
        self.sahari_delay_max_input.setValue(3)
        self.sahari_delay_max_input.setSuffix(" 초")
        self.sahari_delay_max_input.setFixedWidth(60)
        self.sahari_delay_max_input.valueChanged.connect(self.auto_save_sahari_config)
        delay_range_layout.addWidget(self.sahari_delay_max_input)
        settings_layout.addRow("지연 시간:", delay_range_layout)
        # 수동 댓글 입력창
        self.sahari_comments_input = QTextEdit()
        self.sahari_comments_input.setPlaceholderText("댓글을 한 줄에 하나씩 입력하세요\n예시:\n좋은 글이네요!\n정말 유용한 정보입니다.\n감사합니다!")
        self.sahari_comments_input.setMinimumHeight(120)
        self.sahari_comments_input.setMaximumHeight(130)
        self.sahari_comments_input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.sahari_comments_input.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        settings_layout.addRow("수동 댓글:", self.sahari_comments_input)
        # 팔로워 개수
        self.sahari_follow_count_input = QSpinBox()
        self.sahari_follow_count_input.setRange(1, 100)
        self.sahari_follow_count_input.setValue(5)
        self.sahari_follow_count_input.setSuffix(" 명")
        self.sahari_follow_count_input.setFixedWidth(60)
        self.sahari_follow_count_input.valueChanged.connect(self.auto_save_sahari_config)
        settings_layout.addRow("팔로워 개수:", self.sahari_follow_count_input)
        # --- 좋아요 범위 입력 ---
        like_range_layout = QHBoxLayout()
        like_range_layout.setSpacing(4)
        self.sahari_like_min_input = QSpinBox()
        self.sahari_like_min_input.setRange(0, 100)
        self.sahari_like_min_input.setValue(1)
        self.sahari_like_min_input.setSuffix("개")
        self.sahari_like_min_input.setFixedWidth(60)
        self.sahari_like_min_input.valueChanged.connect(self.auto_save_sahari_config)
        like_range_layout.addWidget(self.sahari_like_min_input)
        like_wave = QLabel("~")
        like_wave.setContentsMargins(0,0,0,0)
        like_wave.setFixedWidth(12)
        like_range_layout.addWidget(like_wave)
        self.sahari_like_max_input = QSpinBox()
        self.sahari_like_max_input.setRange(0, 100)
        self.sahari_like_max_input.setValue(3)
        self.sahari_like_max_input.setSuffix("개")
        self.sahari_like_max_input.setFixedWidth(60)
        self.sahari_like_max_input.valueChanged.connect(self.auto_save_sahari_config)
        like_range_layout.addWidget(self.sahari_like_max_input)
        settings_layout.addRow("좋아요 범위:", like_range_layout)
        # --- 리포스트 범위 입력 ---
        repost_range_layout = QHBoxLayout()
        repost_range_layout.setSpacing(4)
        self.sahari_repost_min_input = QSpinBox()
        self.sahari_repost_min_input.setRange(0, 100)
        self.sahari_repost_min_input.setValue(0)
        self.sahari_repost_min_input.setSuffix("개")
        self.sahari_repost_min_input.setFixedWidth(60)
        self.sahari_repost_min_input.valueChanged.connect(self.auto_save_sahari_config)
        repost_range_layout.addWidget(self.sahari_repost_min_input)
        repost_wave = QLabel("~")
        repost_wave.setContentsMargins(0,0,0,0)
        repost_wave.setFixedWidth(12)
        repost_range_layout.addWidget(repost_wave)
        self.sahari_repost_max_input = QSpinBox()
        self.sahari_repost_max_input.setRange(0, 100)
        self.sahari_repost_max_input.setValue(1)
        self.sahari_repost_max_input.setSuffix("개")
        self.sahari_repost_max_input.setFixedWidth(60)
        self.sahari_repost_max_input.valueChanged.connect(self.auto_save_sahari_config)
        repost_range_layout.addWidget(self.sahari_repost_max_input)
        settings_layout.addRow("리포스트 범위:", repost_range_layout)
        # --- 댓글 범위 입력 ---
        comment_range_layout = QHBoxLayout()
        comment_range_layout.setSpacing(4)
        self.sahari_comment_min_input = QSpinBox()
        self.sahari_comment_min_input.setRange(0, 100)
        self.sahari_comment_min_input.setValue(0)
        self.sahari_comment_min_input.setSuffix("개")
        self.sahari_comment_min_input.setFixedWidth(60)
        self.sahari_comment_min_input.valueChanged.connect(self.auto_save_sahari_config)
        comment_range_layout.addWidget(self.sahari_comment_min_input)
        comment_wave = QLabel("~")
        comment_wave.setContentsMargins(0,0,0,0)
        comment_wave.setFixedWidth(12)
        comment_range_layout.addWidget(comment_wave)
        self.sahari_comment_max_input = QSpinBox()
        self.sahari_comment_max_input.setRange(0, 100)
        self.sahari_comment_max_input.setValue(2)
        self.sahari_comment_max_input.setSuffix("개")
        self.sahari_comment_max_input.setFixedWidth(60)
        self.sahari_comment_max_input.valueChanged.connect(self.auto_save_sahari_config)
        comment_range_layout.addWidget(self.sahari_comment_max_input)
        settings_layout.addRow("댓글 범위:", comment_range_layout)
        # 반복 간격 입력
        repeat_interval_layout = QHBoxLayout()
        repeat_interval_layout.setSpacing(4)
        self.sahari_repeat_hour_input = QSpinBox()
        self.sahari_repeat_hour_input.setRange(0, 23)
        self.sahari_repeat_hour_input.setSuffix("시")
        self.sahari_repeat_hour_input.setFixedWidth(60)
        self.sahari_repeat_hour_input.valueChanged.connect(self.auto_save_sahari_config)
        repeat_interval_layout.addWidget(self.sahari_repeat_hour_input)
        self.sahari_repeat_min_input = QSpinBox()
        self.sahari_repeat_min_input.setRange(0, 59)
        self.sahari_repeat_min_input.setSuffix("분")
        self.sahari_repeat_min_input.setFixedWidth(60)
        self.sahari_repeat_min_input.valueChanged.connect(self.auto_save_sahari_config)
        repeat_interval_layout.addWidget(self.sahari_repeat_min_input)
        self.sahari_repeat_none_checkbox = QCheckBox("반복 없음")
        self.sahari_repeat_none_checkbox.setChecked(False)
        self.sahari_repeat_none_checkbox.stateChanged.connect(self._on_sahari_repeat_none_changed)
        repeat_interval_layout.addWidget(self.sahari_repeat_none_checkbox)
        settings_layout.addRow("반복 간격:", repeat_interval_layout)
        layout.addWidget(settings_group)
        
        # 버튼 그룹
        button_group = QGroupBox("🎯 스하리 실행")
        button_layout = QHBoxLayout(button_group)
        
        self.sahari_start_all_btn = QPushButton("🚀 전체 스하리 시작")
        self.sahari_start_all_btn.clicked.connect(self.start_all_sahari)
        button_layout.addWidget(self.sahari_start_all_btn)
        
        self.sahari_stop_all_btn = QPushButton("⏹️ 전체 스하리 중지")
        self.sahari_stop_all_btn.setEnabled(False)
        self.sahari_stop_all_btn.clicked.connect(self.stop_all_sahari)
        button_layout.addWidget(self.sahari_stop_all_btn)
        
        button_layout.addStretch()
        layout.addWidget(button_group)
        
        # 스하리 상태 테이블
        status_group = QGroupBox("📊 스하리 실행 상태")
        status_layout = QVBoxLayout(status_group)
        
        # 상태 테이블 버튼 레이아웃
        table_button_layout = QHBoxLayout()
        
        self.sahari_status_table = QTableWidget(0, 8)
        self.sahari_status_table.setHorizontalHeaderLabels(["계정", "팔로워 완료", "좋아요 완료", "리포스트 완료", "댓글 완료", "상태", "진행률", "작업"])
        header = self.sahari_status_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # 계정
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 팔로워 완료
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 좋아요 완료
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 리포스트 완료
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 댓글 완료
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 상태
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 진행률
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 작업
        
        # 초기화 버튼
        reset_status_btn = QPushButton("🗑️ 스하리 실행 상태 초기화")
        reset_status_btn.clicked.connect(self.reset_sahari_status)
        table_button_layout.addWidget(reset_status_btn)
        
        table_button_layout.addStretch()
        status_layout.addLayout(table_button_layout)
        status_layout.addWidget(self.sahari_status_table)
        
        layout.addWidget(status_group)
        
        # 설정 저장/불러오기 (자동 저장으로 인해 수동 버튼은 제거하고 정보 표시)
        config_layout = QHBoxLayout()
        config_info_label = QLabel("💡 설정은 자동으로 저장됩니다")
        config_info_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        config_layout.addWidget(config_info_label)
        
        config_layout.addStretch()
        layout.addLayout(config_layout)

    def setup_right_panel(self, parent_splitter):
        """오른쪽 탭 패널 (게시글 등록, 로그)"""
        right_widget = QWidget()
        layout = QVBoxLayout(right_widget)
        self.right_tabs = QTabWidget()
        layout.addWidget(self.right_tabs)
        
        # 1. 실시간 로그 탭
        log_tab = QWidget()
        self.setup_log_tab(log_tab)
        self.right_tabs.addTab(log_tab, "📋 실시간 로그")

        # 2. 스하리 로그 탭
        sahari_log_tab = QWidget()
        self.setup_sahari_log_tab(sahari_log_tab)
        self.right_tabs.addTab(sahari_log_tab, "🎯 스하리 로그")

        # 3. 게시글 등록/수정 탭
        post_edit_tab = QWidget()
        self.setup_post_edit_tab(post_edit_tab)
        self.right_tabs.addTab(post_edit_tab, "✍️ 게시글 등록/수정")
        
        # 4. 실시간 통계 탭 (분리)
        stats_tab = QWidget()
        self.setup_stats_tab(stats_tab)
        self.right_tabs.addTab(stats_tab, "📊 실시간 통계")

        parent_splitter.addWidget(right_widget)

    def setup_post_edit_tab(self, parent_tab):
        layout = QVBoxLayout(parent_tab)
        # 게시물 유형, 게시물, 원형체크박스(regular_post_radio) 주석처리
        # self.post_type_group = QGroupBox("게시물 유형")
        # post_type_layout = QHBoxLayout()
        # self.regular_post_radio = QRadioButton("게시몰")
        # self.regular_post_radio.setChecked(True)
        # post_type_layout.addWidget(self.regular_post_radio)
        # self.post_type_group.setLayout(post_type_layout)
        # layout.addWidget(self.post_type_group)
        # self.regular_post_radio.toggled.connect(self.on_post_type_changed)

        # 일반 게시물 입력 필드
        self.regular_post_fields = QGroupBox("일반 게시물 정보")
        regular_layout = QFormLayout(self.regular_post_fields)
        self.post_title_input = QLineEdit()
        self.post_title_input.setPlaceholderText("제목을 입력하세요 (내부 관리용)")
        regular_layout.addRow("제목:", self.post_title_input)
        self.post_content_input = QTextEdit()
        self.post_content_input.setPlaceholderText("게시할 내용을 입력하세요...")
        self.post_content_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        regular_layout.addRow("내용:", self.post_content_input)
        # 사진 입력 필드와 업로드 버튼
        image_layout = QHBoxLayout()
        self.post_image_url_input = QLineEdit()
        self.post_image_url_input.setPlaceholderText("사진 URL 입력 (여러 개인 경우 쉼표(,)로 구분)")
        image_upload_btn = QPushButton("📁 업로드")
        image_upload_btn.clicked.connect(self.upload_images)
        image_upload_btn.setFixedHeight(30)
        image_layout.addWidget(self.post_image_url_input)
        image_layout.addWidget(image_upload_btn)
        regular_layout.addRow("사진:", image_layout)
        
        # 동영상 입력 필드와 업로드 버튼
        video_layout = QHBoxLayout()
        self.post_video_url_input = QLineEdit()
        self.post_video_url_input.setPlaceholderText("동영상 URL 입력")
        video_upload_btn = QPushButton("📁 업로드")
        video_upload_btn.clicked.connect(self.upload_video)
        video_upload_btn.setFixedHeight(30)
        video_layout.addWidget(self.post_video_url_input)
        video_layout.addWidget(video_upload_btn)
        regular_layout.addRow("영상:", video_layout)
        layout.addWidget(self.regular_post_fields)

        # 슬라이드 게시물 입력 필드 (초기에는 숨김)
        self.slide_post_fields = QGroupBox("슬라이드 게시물 정보")
        slide_layout = QFormLayout(self.slide_post_fields)
        self.slide_post_title_input = QLineEdit()
        self.slide_post_title_input.setPlaceholderText("게시글을 식별할 제목을 입력하세요 (내부 관리용)")
        slide_layout.addRow("게시글 제목:", self.slide_post_title_input)
        self.slide_post_content_input = QTextEdit()
        self.slide_post_content_input.setPlaceholderText("게시할 내용을 입력하세요...")
        self.slide_post_content_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        slide_layout.addRow("내용:", self.slide_post_content_input)
        self.slide_media_input = QTextEdit()
        self.slide_media_input.setPlaceholderText("이미지 또는 동영상 URL을 한 줄에 하나씩 입력하세요.\n.jpg, .png, .jpeg 등은 이미지로, .mp4, .mov 등은 동영상으로 자동 인식됩니다.")
        self.slide_media_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        slide_layout.addRow("미디어 URL 목록:", self.slide_media_input)
        self.slide_post_fields.setLayout(slide_layout)
        layout.addWidget(self.slide_post_fields)
        self.slide_post_fields.hide()

        layout.addStretch()

        # 버튼 그룹
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 버튼 크기를 칸에 맞춰 설정
        button_height = 40  # 고정 높이
        
        self.post_submit_btn = QPushButton("📝 게시글 등록")
        self.post_submit_btn.setFixedHeight(button_height)
        self.post_submit_btn.clicked.connect(self.submit_post)
        
        excel_btn = QPushButton("📊 엑셀로 등록")
        excel_btn.setFixedHeight(button_height)
        excel_btn.clicked.connect(self.import_posts_from_excel)
        
        button_layout.addWidget(self.post_submit_btn)
        button_layout.addWidget(excel_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
    def on_post_type_changed(self):
        is_regular = self.regular_post_radio.isChecked()
        self.regular_post_fields.setVisible(is_regular)
        self.slide_post_fields.setVisible(not is_regular)

        # 필드 값 동기화
        if is_regular:
            self.post_title_input.setText(self.slide_post_title_input.text())
            self.post_content_input.setPlainText(self.slide_post_content_input.toPlainText())
        else:
            self.slide_post_title_input.setText(self.post_title_input.text())
            self.slide_post_content_input.setPlainText(self.post_content_input.toPlainText())

    def setup_log_tab(self, parent_tab):
        # 로그 탭 UI (통계 패널 분리 후)
        layout = QVBoxLayout(parent_tab)
        self.log_text = QTextEdit(readOnly=True)
        layout.addWidget(self.log_text)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 버튼 크기를 칸에 맞춰 설정
        button_height = 35  # 고정 높이
        
        clear_log_btn = QPushButton("🗑️ 로그 지우기")
        clear_log_btn.setFixedHeight(button_height)
        clear_log_btn.clicked.connect(self.log_text.clear)
        
        save_log_btn = QPushButton("💾 로그 저장")
        save_log_btn.setFixedHeight(button_height)
        save_log_btn.clicked.connect(self.save_log)
        
        button_layout.addWidget(clear_log_btn)
        button_layout.addWidget(save_log_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
    def setup_sahari_log_tab(self, parent_tab):
        """스하리 로그 탭 UI 구성"""
        layout = QVBoxLayout(parent_tab)
        self.sahari_log_text = QTextEdit(readOnly=True)
        layout.addWidget(self.sahari_log_text)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 버튼 크기를 칸에 맞춰 설정
        button_height = 35  # 고정 높이
        
        clear_sahari_log_btn = QPushButton("🗑️ 스하리 로그 지우기")
        clear_sahari_log_btn.setFixedHeight(button_height)
        clear_sahari_log_btn.clicked.connect(self.sahari_log_text.clear)
        
        save_sahari_log_btn = QPushButton("💾 스하리 로그 저장")
        save_sahari_log_btn.setFixedHeight(button_height)
        save_sahari_log_btn.clicked.connect(self.save_sahari_log)
        
        button_layout.addWidget(clear_sahari_log_btn)
        button_layout.addWidget(save_sahari_log_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
    def setup_stats_tab(self, parent_tab):
        # 통계 탭 UI
        layout = QVBoxLayout(parent_tab)
        stats_group_box = self.setup_stats_panel()
        layout.addWidget(stats_group_box)
        layout.addStretch() # 상단에 고정

    def setup_stats_panel(self):
        stats_group_box = QGroupBox("📊 실시간 통계")
        stats_layout = QFormLayout(stats_group_box)
        
        self.stats_total_accounts = QLabel("0")
        self.stats_selected_accounts = QLabel("0")
        self.stats_completed = QLabel("0")
        self.stats_in_progress = QLabel("0")
        self.stats_waiting = QLabel("0")
        self.stats_total_posts = QLabel("0")

        stats_layout.addRow("총 계정:", self.stats_total_accounts)
        stats_layout.addRow("선택 계정:", self.stats_selected_accounts)
        stats_layout.addRow("✔️ 작업 완료:", self.stats_completed)
        stats_layout.addRow("🏃 작업 진행:", self.stats_in_progress)
        stats_layout.addRow("⏳ 작업 대기:", self.stats_waiting)
        
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        stats_layout.addRow(separator)
        
        stats_layout.addRow("총 게시글:", self.stats_total_posts)
        
        return stats_group_box

    def setup_status_bar(self, parent_layout):
        """하단 상태바"""
        status_frame = QFrame()
        status_frame.setFrameStyle(QFrame.StyledPanel)
        status_frame.setFixedHeight(30)
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 5, 10, 5)
        # --- 기존 상태 라벨 ---
        self.status_label = QLabel("✅ 준비 완료")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        # --- 날짜 라벨 ---
        self.time_label = QLabel()
        self.update_time()
        # --- CPU/MEM 상태 라벨(날짜 왼쪽에 위치) ---
        self.sysinfo_label = QLabel("CPU 사용률: --%  메모리 사용률: --%")
        self.sysinfo_label.setStyleSheet("color: #666; font-weight: bold;")
        status_layout.addWidget(self.sysinfo_label)
        status_layout.addWidget(self.time_label)
        parent_layout.addWidget(status_frame)
        # --- CPU/MEM 모니터링 타이머 추가 ---
        self.cpu_mem_timer = QTimer(self)
        self.cpu_mem_timer.timeout.connect(self.update_sysinfo_and_check)
        self.cpu_mem_timer.start(1000)
        self._sysinfo_popup = None
        self._sysinfo_popup_state = {'cpu': False, 'mem': False}
    
    def load_account_data(self):
        """계정 데이터를 테이블에 로드합니다."""
        for i, account in enumerate(self.accounts):
            # 0번 열: 체크박스
            checkbox = QCheckBox()
            checkbox.setChecked(account.get('checked', True))
            checkbox.setMinimumHeight(26)
            checkbox.setMaximumHeight(26)
            checkbox.stateChanged.connect(lambda state, idx=i: self.update_account_field(idx, 'checked', state == Qt.Checked))
            self.account_table.setCellWidget(i, 0, self.wrap_widget_in_layout(checkbox))

            # 1~6번 열: 계정정보(아이디, 비번, api_id, 토큰, 프록시ip, 포트)
            fields = ['username', 'password', 'api_id', 'token', 'proxy_ip', 'proxy_port']
            for j, field in enumerate(fields):
                editor = QLineEdit(str(account.get(field, '')))
                editor.setMinimumHeight(26)
                editor.setMaximumHeight(26)
                if field == 'password':
                    if not hasattr(self, '_password_editors'):
                        self._password_editors = {}
                    self._password_editors[i] = editor
                    editor.setEchoMode(QLineEdit.Normal if getattr(self, 'password_visible', False) else QLineEdit.Password)
                editor.textChanged.connect(lambda text, idx=i, f=field: self.update_account_field(idx, f, text))
                self.account_table.setCellWidget(i, j + 1, self.wrap_widget_in_layout(editor))

            # 7번 열: 상태(QLabel)
            status_label = QLabel(account.get('status', '대기중'))
            status_label.setAlignment(Qt.AlignCenter)
            status_label.setMinimumHeight(26)
            status_label.setMaximumHeight(26)
            self.account_table.setCellWidget(i, 7, self.wrap_widget_in_layout(status_label))

            # 8번 열: 런처 버튼
            launcher_btn = QPushButton("실행")
            launcher_btn.setObjectName("tableButton")
            launcher_btn.setMinimumHeight(26)
            launcher_btn.setMaximumHeight(26)
            launcher_btn.clicked.connect(lambda ch, idx=i: self.launch_browser_for_account(idx))
            self.account_table.setCellWidget(i, 8, self.wrap_widget_in_layout(launcher_btn))

            # 9번 열: 스하리 시작 버튼
            sahari_btn = QPushButton("시작")
            sahari_btn.setObjectName("sahariButton")
            sahari_btn.setMinimumHeight(26)
            sahari_btn.setMaximumHeight(26)
            sahari_btn.clicked.connect(lambda ch, idx=i: self.start_sahari_for_account(idx))
            self.account_table.setCellWidget(i, 9, self.wrap_widget_in_layout(sahari_btn))

            self.account_table.setRowHeight(i, 26)

    def wrap_widget_in_layout(self, widget):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.addWidget(widget)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        container.setFixedHeight(26)
        container.setMaximumHeight(26)
        return container

    def update_account_field(self, index, field, value):
        """계정 정보를 업데이트하고 파일에 저장합니다."""
        self.accounts[index][field] = value
        self.save_data_to_file('accounts.json', self.accounts)

    def load_post_data(self):
        """게시글 데이터를 테이블에 로드 (반복 필드 자동 보강)"""
        for post in self.posts:
            if 'repeat_count' not in post:
                post['repeat_count'] = 1
            if 'repeat_progress' not in post:
                post['repeat_progress'] = 0
        self.save_data_to_file('posts.json', self.posts)
        self.post_table.setRowCount(len(self.posts))
        for i, post in enumerate(self.posts):
            checkbox = QCheckBox()
            checkbox.setChecked(post.get('checked', False))
            checkbox.setMinimumHeight(30)
            checkbox.setMaximumHeight(30)
            checkbox.stateChanged.connect(lambda state, idx=i: self.update_post_field(idx, 'checked', state == Qt.Checked))
            self.post_table.setCellWidget(i, 0, self.wrap_widget_in_layout(checkbox))
            self.post_table.setItem(i, 1, QTableWidgetItem(str(i + 1)))
            self.post_table.setItem(i, 2, QTableWidgetItem(post['title']))
            self.post_table.setItem(i, 3, QTableWidgetItem(post['content']))
            self.post_table.setItem(i, 4, QTableWidgetItem(post.get('image_url', '')))
            self.post_table.setItem(i, 5, QTableWidgetItem(post.get('video_url', '')))
            repeat_count = post.get('repeat_count', 1)
            repeat_progress = post.get('repeat_progress', 0)
            if post['status'] == '완료':
                status_text = '완료'
            elif repeat_count > 1:
                status_text = f"{repeat_progress}/{repeat_count} 완료"
            else:
                status_text = post['status']
            self.update_post_status(i, status_text)
            edit_btn = QPushButton("수정")
            edit_btn.setObjectName("tableButton")
            edit_btn.setMinimumHeight(30)
            edit_btn.setMaximumHeight(30)
            edit_btn.clicked.connect(lambda ch, idx=i: self.edit_post(idx))
            self.post_table.setCellWidget(i, 7, self.wrap_widget_in_layout(edit_btn))
            delete_btn = QPushButton("삭제")
            delete_btn.setObjectName("tableButton")
            delete_btn.setMinimumHeight(30)
            delete_btn.setMaximumHeight(30)
            delete_btn.clicked.connect(lambda ch, idx=i: self.delete_post(idx))
            self.post_table.setCellWidget(i, 8, self.wrap_widget_in_layout(delete_btn))
            repeat_btn = QPushButton("반복")
            repeat_btn.setObjectName("tableButton")
            repeat_btn.setMinimumHeight(30)
            repeat_btn.setMaximumHeight(30)
            repeat_btn.clicked.connect(lambda ch, idx=i: self.open_repeat_dialog(idx))
            self.post_table.setCellWidget(i, 9, self.wrap_widget_in_layout(repeat_btn))
            self.post_table.setRowHeight(i, 30)

    def update_post_field(self, index, field, value):
        self.posts[index][field] = value
        self.save_data_to_file('posts.json', self.posts)

    def toggle_all_post_checkboxes(self, state):
        """모든 게시글 체크박스 토글 (위젯 안전성 강화)"""
        try:
            if not hasattr(self, 'post_table') or self.post_table is None:
                return
                
            is_checked = (state == Qt.Checked)
            row_count = self.post_table.rowCount()
            
            for i in range(row_count):
                try:
                    container_widget = self.post_table.cellWidget(i, 0)
                    if container_widget:
                        checkbox = container_widget.findChild(QCheckBox)
                        if checkbox:  # isNull() 제거
                            if checkbox.isChecked() != is_checked:
                                checkbox.setChecked(is_checked)
                except (RuntimeError, AttributeError):
                    # 위젯이 삭제되었거나 접근할 수 없는 경우 무시
                    continue
        except Exception as e:
            self.add_log(f"❌ 게시글 체크박스 토글 중 오류: {e}")

    def delete_selected_posts(self):
        """체크된 게시글만 삭제"""
        indices_to_delete = [i for i, post in enumerate(self.posts) if post.get('checked', False)]
        if not indices_to_delete:
            QMessageBox.information(self, "알림", "선택된 게시글이 없습니다.")
            return
        if QMessageBox.question(self, "선택삭제 확인", f"선택된 {len(indices_to_delete)}개 게시글을 삭제하시겠습니까?") == QMessageBox.Yes:
            for idx in sorted(indices_to_delete, reverse=True):
                del self.posts[idx]
            self.save_data_to_file('posts.json', self.posts)
            self.load_post_data()

    def reset_selected_status(self):
        """체크된 게시글만 '대기중'으로, 아무것도 선택 안 하면 전체 초기화 여부 팝업"""
        indices_to_reset = [i for i, post in enumerate(self.posts) if post.get('checked', False)]
        if not indices_to_reset:
            reply = QMessageBox.question(self, "상태초기화", "선택된 게시글이 없습니다. 전체 게시글을 '대기중'으로 초기화하시겠습니까?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                for post in self.posts:
                    post['status'] = '대기중'
                    post['repeat_progress'] = 0  # 반복 진행도도 리셋
                self.save_data_to_file('posts.json', self.posts)
                self.load_post_data()
            return
        for idx in indices_to_reset:
            self.posts[idx]['status'] = '대기중'
            self.posts[idx]['repeat_progress'] = 0  # 반복 진행도도 리셋
        self.save_data_to_file('posts.json', self.posts)
        self.load_post_data()

    def submit_post(self):
        """게시글 저장/수정 동작. 라디오 버튼 없이 일반 게시물 입력만 사용하도록 고정."""
        # 라디오 버튼 분기 제거: 무조건 일반 게시물 입력 필드 사용
        is_regular = True

        if is_regular:
            title = self.post_title_input.text().strip()
            content = self.post_content_input.toPlainText().strip()
            image_url = self.post_image_url_input.text().strip()
            video_url = self.post_video_url_input.text().strip()
            
            if not title:
                QMessageBox.warning(self, "입력 오류", "게시글 제목을 입력해주세요.")
                return

            post_data = {
                "title": title,
                "content": content,
                "image_url": image_url,
                "video_url": video_url,
                "post_type": "regular",
                "status": "대기중",
                "repeat_count": 1,
                "repeat_progress": 0
            }

        if self.editing_post_index is not None:
            # 수정 시 기존 반복 설정 보존
            existing_post = self.posts[self.editing_post_index]
            post_data['repeat_count'] = existing_post.get('repeat_count', 1)
            post_data['repeat_progress'] = existing_post.get('repeat_progress', 0)
            self.posts[self.editing_post_index] = post_data
            self.editing_post_index = None
            self.add_log(f"✏️ 게시글이 수정되었습니다: {title}")
        else:
            self.posts.append(post_data)
            self.add_log(f"📝 새 게시글이 등록되었습니다: {title}")
        self.save_data_to_file('posts.json', self.posts)
        self.load_post_data()
        self.post_title_input.clear()
        self.post_content_input.clear()
        self.post_image_url_input.clear()
        self.post_video_url_input.clear()
        self.post_submit_btn.setText("📝 게시글 등록")

    def edit_post(self, index):
        """게시글 데이터를 편집 폼으로 불러옵니다."""
        if 0 <= index < len(self.posts):
            self.editing_post_index = index
            post_data = self.posts[index]
            self.post_submit_btn.setText("✏️ 게시글 수정")
            
            post_type = post_data.get('post_type', 'regular')

            if post_type == 'slide':
                self.slide_post_fields.setVisible(True)
                self.slide_post_title_input.setText(post_data.get('title', ''))
                self.slide_post_content_input.setPlainText(post_data.get('content', ''))
                media_items = post_data.get('media_items', [])
                urls = [item['url'] for item in media_items]
                self.slide_media_input.setPlainText('\n'.join(urls))
                # 반대편도 채워주기
                self.post_title_input.setText(post_data.get('title', ''))
                self.post_content_input.setPlainText(post_data.get('content', ''))
                self.post_image_url_input.clear()
                self.post_video_url_input.clear()
            else: # regular
                self.slide_post_fields.setVisible(False)
                self.post_title_input.setText(post_data.get('title', ''))
                self.post_content_input.setPlainText(post_data.get('content', ''))
                self.post_image_url_input.setText(post_data.get('image_url', ''))
                self.post_video_url_input.setText(post_data.get('video_url', ''))
                # 반대편도 채워주기
                self.slide_post_title_input.setText(post_data.get('title', ''))
                self.slide_post_content_input.setPlainText(post_data.get('content', ''))
                self.slide_media_input.clear()

            self.right_tabs.setCurrentIndex(1) # 편집 탭으로 이동
            self.add_log(f"✍️ 게시글 '{post_data.get('title', '')}' 수정 시작...")

    def delete_post(self, index):
        """선택한 게시글을 삭제합니다."""
        if QMessageBox.question(self, "삭제 확인", f"'{self.posts[index]['title']}' 게시글을 삭제하시겠습니까?") == QMessageBox.Yes:
            del self.posts[index]
            self.save_data_to_file('posts.json', self.posts)
            self.load_post_data()

    def delete_all_posts(self):
        """모든 게시글 삭제"""
        if QMessageBox.question(self, "전체 삭제 확인", "모든 게시글을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.") == QMessageBox.Yes:
            self.posts.clear()
            self.save_data_to_file('posts.json', self.posts)
            self.load_post_data()

    def import_posts_from_excel(self):
        """새로운 양식에 맞춰 엑셀에서 게시글을 대량으로 가져옵니다."""
        file_path, _ = QFileDialog.getOpenFileName(self, "엑셀 파일 선택", "", "Excel Files (*.xlsx *.xls)")
        if not file_path:
            return
        
        try:
            # 2행부터 데이터가 시작되므로, 첫 번째 행(헤더)은 건너뜁니다.
            df = pd.read_excel(file_path, header=None, skiprows=1, dtype=str).fillna('')
            
            new_posts = []
            for _, row in df.iterrows():
                title = row.get(0, '')      # A열: 주제
                content = row.get(1, '')    # B열: 본문내용

                # C열부터 L열까지 이미지 URL을 수집하여 콤마로 연결
                image_urls = []
                for i in range(2, 12): # 인덱스 2(C)부터 11(L)까지
                    url = row.get(i, '')
                    if url: # URL이 비어있지 않은 경우에만 추가
                        # 로컬 파일 경로인지 확인하고 Catbox.moe로 업로드
                        if os.path.isfile(url.strip()):
                            try:
                                self.add_log(f"📁 로컬 파일 발견: {url}")
                                uploaded_url = catbox_uploader.upload_file(url.strip())
                                image_urls.append(uploaded_url)
                                self.add_log(f"✅ Catbox.moe 업로드 성공: {uploaded_url}")
                            except Exception as e:
                                self.add_log(f"❌ 파일 업로드 실패: {url} - {e}")
                                # 업로드 실패 시 원본 경로 유지
                                image_urls.append(url)
                        else:
                            # 이미 URL인 경우 그대로 사용
                            image_urls.append(url)
                image_url_str = " , ".join(image_urls)

                video_url = row.get(12, '') # M열: 동영상 URL
                # 동영상도 로컬 파일 경로인지 확인하고 업로드
                if video_url and os.path.isfile(video_url.strip()):
                    try:
                        self.add_log(f"📁 동영상 파일 발견: {video_url}")
                        uploaded_video_url = catbox_uploader.upload_file(video_url.strip())
                        video_url = uploaded_video_url
                        self.add_log(f"✅ 동영상 업로드 성공: {uploaded_video_url}")
                    except Exception as e:
                        self.add_log(f"❌ 동영상 업로드 실패: {video_url} - {e}")
                        # 업로드 실패 시 원본 경로 유지
                
                # 데이터가 하나라도 있는 행만 추가 (완전히 빈 행은 무시)
                if title or content or image_url_str or video_url:
                    new_posts.append({
                        'title': title, 
                        'content': content, 
                        'image_url': image_url_str, 
                        'video_url': video_url,
                        'status': '대기중',
                        'post_type': 'regular', # 엑셀은 일반만 지원
                        'repeat_count': 1,
                        'repeat_progress': 0
                    })
            
            self.posts.extend(new_posts)
            self.save_data_to_file('posts.json', self.posts)
            self.load_post_data()
            self.add_log(f"📊 엑셀에서 {len(new_posts)}개 게시글을 성공적으로 가져왔습니다.")
        except Exception as e:
            QMessageBox.critical(self, "오류", f"엑셀 파일 읽기 실패: {e}")

    # --- 로깅 및 상태 관리 ---
    def add_log(self, message):
        """로그 메시지를 UI에 추가합니다."""
        if hasattr(self, 'log_text'):
            self.log_text.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        
        # 크래시 로거에도 로그 추가 (비정상 종료 시 저장용)
        crash_logger.add_log(message)
    
    def add_sahari_log(self, message):
        """스하리 로그 메시지를 UI에 추가합니다."""
        if hasattr(self, 'sahari_log_text'):
            self.sahari_log_text.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        
        # 크래시 로거에도 로그 추가 (비정상 종료 시 저장용)
        crash_logger.add_log(message)

    def save_log(self):
        """로그를 파일로 저장합니다."""
        filename, _ = QFileDialog.getSaveFileName(self, "로그 저장", f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", "Text Files (*.txt)")
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f: f.write(self.log_text.toPlainText())
                self.add_log(f"💾 로그가 {filename}에 저장되었습니다.")
            except Exception as e:
                self.add_log(f"❌ 로그 저장 실패: {e}")
    
    def save_sahari_log(self):
        """스하리 로그를 파일로 저장합니다."""
        filename, _ = QFileDialog.getSaveFileName(self, "스하리 로그 저장", f"sahari_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", "Text Files (*.txt)")
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f: f.write(self.sahari_log_text.toPlainText())
                self.add_sahari_log(f"💾 스하리 로그가 {filename}에 저장되었습니다.")
            except Exception as e:
                self.add_sahari_log(f"❌ 스하리 로그 저장 실패: {e}")
    
    def start_all_accounts(self):
        """순차적 작업 로직을 시작합니다."""
        # --- 프록시 누락 계정 검사 및 팝업 처리 추가 시작 ---
        checked_accounts = [(i, acc) for i, acc in enumerate(self.accounts) if acc.get('checked', False)]
        ip_missing = []
        port_missing = []
        for idx, acc in checked_accounts:
            if not acc.get('proxy_ip', '').strip():
                ip_missing.append(idx+1)  # 1번부터 시작
            if not acc.get('proxy_port', '').strip():
                port_missing.append(idx+1)
        if ip_missing or port_missing:
            msg = ""
            if ip_missing:
                msg += f"{','.join(str(n) for n in ip_missing)}번 ip 설정 누락\n"
            if port_missing:
                msg += f"{','.join(str(n) for n in port_missing)}번 port 설정 누락\n"
            msg += "\n기본ip로 실행 하시겠습니까?\n해당 계정 제외 후 실행 하시겠습니까?"
            dlg = QMessageBox(self)
            dlg.setWindowTitle("프록시 정보 누락")
            dlg.setText(msg)
            기본실행 = dlg.addButton("기본실행", QMessageBox.AcceptRole)
            제외실행 = dlg.addButton("제외실행", QMessageBox.DestructiveRole)
            실행취소 = dlg.addButton("실행취소", QMessageBox.RejectRole)
            dlg.setIcon(QMessageBox.Warning)
            dlg.exec_()
            if dlg.clickedButton() == 실행취소:
                self.add_log("⚠️ 프록시 누락 계정 팝업에서 '실행취소' 선택. 작업을 중단합니다.")
                return
            elif dlg.clickedButton() == 제외실행:
                # 프록시 정보가 모두 채워진 계정만 남김
                checked_accounts = [item for item in checked_accounts if item[0]+1 not in set(ip_missing) and item[0]+1 not in set(port_missing)]
                self.add_log("⚠️ 프록시 누락 계정 제외 후 실행합니다.")
            else:
                self.add_log("⚠️ 프록시 누락 계정도 기본IP로 실행합니다.")
        # --- 프록시 누락 계정 검사 및 팝업 처리 추가 끝 ---

        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "작업 중", "이미 작업이 진행 중입니다.")
            return

        # 대기중인 게시글 + 반복 진행 중인 게시글 모두 포함 (checked_accounts 확정 후에 위치)
        waiting_posts = []
        for i, post in enumerate(self.posts):
            status = post.get('status', '대기중')
            repeat_count = post.get('repeat_count', 1)
            repeat_progress = post.get('repeat_progress', 0)
            # 대기중이거나 완료되지 않은 반복 작업인 경우 포함
            if status == '대기중' or (repeat_count > 1 and repeat_progress < repeat_count):
                waiting_posts.append((i, post))

        if not waiting_posts:
            QMessageBox.warning(self, "시작 오류", "대기중인 게시글이 없습니다.")
            return
            
        posts_to_process = waiting_posts
        if self.settings.get('repeat_interval', 0) == 0:
            posts_to_process = waiting_posts[:1]
            self.add_log("ℹ️ 반복 없음 설정: 첫 번째 대기중인 게시글만 처리합니다.")

        self.start_all_btn.setEnabled(False)
        self.stop_all_btn.setEnabled(True)
        self.add_log("🚀 순차 작업 시작!")
        
        # 선택된 계정만 대기중으로 초기화 (선택되지 않은 계정은 이전 상태 유지)
        for i, account in enumerate(self.accounts):
            if account.get('checked', False):
                self.update_account_status(i, "대기중")

        self.worker = ParallelWorker(checked_accounts, posts_to_process, self.settings, self)
        self.worker.log_updated.connect(self.add_log)
        self.worker.account_status_updated.connect(self.update_account_status)
        self.worker.post_status_updated.connect(self.update_post_status_and_cleanup)
        self.worker.process_finished.connect(self.on_worker_finished)
        # 새로운 안전한 시그널 연결
        self.worker.save_posts_data.connect(self.safe_save_posts_data)
        self.worker.post_status_update.connect(self.safe_update_post_status)
        self.worker.start()

    def stop_all_accounts(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        else:
            self.on_worker_finished() # 즉시 UI 정리

    def on_worker_finished(self):
        self.add_log("🏁 모든 작업이 종료되었습니다.")
        self.start_all_btn.setEnabled(True)
        self.stop_all_btn.setEnabled(False)
        self.worker = None
        self.cleanup_deleted_posts()
        
        # 작업 완료 후 진행 중인 계정만 대기중으로 정리 (실패한 계정은 유지)
        for i in range(len(self.accounts)):
            current_status = self.accounts[i].get('status', '대기중')
            if "작업 중" in current_status or "진행" in current_status:
                self.update_account_status(i, "대기중")

    def safe_save_posts_data(self, post_index, repeat_progress):
        """워커 쓰레드에서 안전하게 posts 데이터 저장"""
        try:
            if 0 <= post_index < len(self.posts):
                self.posts[post_index]['repeat_progress'] = repeat_progress
                self.save_data_to_file('posts.json', self.posts)
        except Exception as e:
            self.add_log(f"❌ 게시글 데이터 저장 실패: {e}")

    def safe_update_post_status(self, post_index, status):
        """워커 쓰레드에서 안전하게 posts 상태 업데이트"""
        try:
            if 0 <= post_index < len(self.posts):
                self.posts[post_index]['status'] = status
                if status == '완료':
                    self.posts[post_index]['repeat_progress'] = 0
                self.save_data_to_file('posts.json', self.posts)
        except Exception as e:
            self.add_log(f"❌ 게시글 상태 업데이트 실패: {e}")

    def update_post_status_and_cleanup(self, post_index, status_text):
        """게시글 상태 업데이트 및 자동 삭제 처리 (인덱스 범위 검증 강화)"""
        try:
            if not (0 <= post_index < len(self.posts)):
                self.add_log(f"⚠️ 잘못된 게시글 인덱스: {post_index} (최대: {len(self.posts)-1})")
                return
                
            self.update_post_status(post_index, status_text)

            if status_text == "완료" and self.settings.get('auto_delete_completed_posts', False):
                if post_index < len(self.posts) and self.posts[post_index] is not None:
                    post_title = self.posts[post_index].get('title', f'게시글{post_index}')
                    self.add_log(f"✅ 작업 완료된 게시글 '{post_title}'을(를) 자동 삭제 대상으로 표시합니다.")
                    self.posts[post_index] = None
                    
                    if not hasattr(self, 'cleanup_timer'):
                        self.cleanup_timer = QTimer(self)
                        self.cleanup_timer.setSingleShot(True)
                        self.cleanup_timer.timeout.connect(self.cleanup_deleted_posts)
                    self.cleanup_timer.start(500)
        except Exception as e:
            self.add_log(f"❌ 게시글 상태 업데이트 중 오류: {e}")

    def cleanup_deleted_posts(self):
        """None으로 표시된 게시글을 실제로 제거하고 UI를 새로고침 (동시성 안전)"""
        try:
            original_count = len(self.posts)
            # None이 아닌 항목만 필터링 (깊은 복사 방식으로 안전하게)
            valid_posts = []
            for post in self.posts:
                if post is not None:
                    valid_posts.append(post)
            
            self.posts = valid_posts
            deleted_count = original_count - len(self.posts)

            if deleted_count > 0:
                self.save_data_to_file('posts.json', self.posts)
                
                # UI 새로고침을 안전하게 실행
                if hasattr(self, 'post_table') and self.post_table is not None:
                    self.load_post_data()
                    
                self.add_log(f"🗑️ 완료된 게시글 {deleted_count}개를 목록에서 정리했습니다.")
        except Exception as e:
            self.add_log(f"❌ 게시글 정리 중 오류: {e}")

    def open_settings(self):
        """설정 다이얼로그를 엽니다."""
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_() == QDialog.Accepted:
            self.settings = dialog.get_settings()
            self.save_settings()
            self.add_log("⚙️ 설정이 저장되었습니다.")
    
    def open_token_gui(self):
        """토큰 발행 GUI를 실행합니다."""
        try:
            self.token_window = ThreadsTokenGUI()
            self.token_window.show()
            self.add_log("🔑 토큰 발행 프로그램을 실행했습니다.")
        except Exception as e:
            QMessageBox.critical(self, "실행 오류", f"토큰 발행 프로그램 실행 중 오류가 발생했습니다:\n{e}")
            self.add_log(f"❌ 토큰 발행 프로그램 실행 실패: {e}")
        
    def update_statistics(self):
        self.update_time()
        if hasattr(self, 'stats_total_accounts'):
            self.update_stats_display()
        
    def update_stats_display(self):
        total_accounts = len(self.accounts)
        selected_accounts = sum(1 for acc in self.accounts if acc.get('checked', False))
        completed = sum(1 for acc in self.accounts if acc.get('status') == '완료')
        in_progress = sum(1 for acc in self.accounts if "진행" in acc.get('status', '') or "작업 중" in acc.get('status', ''))
        waiting = sum(1 for acc in self.accounts if acc.get('status') == '대기중')
        total_posts = len(self.posts)

        self.stats_total_accounts.setText(f"{total_accounts} 개")
        self.stats_selected_accounts.setText(f"{selected_accounts} 개")
        self.stats_completed.setText(f"{completed} 개")
        self.stats_in_progress.setText(f"{in_progress} 개")
        self.stats_waiting.setText(f"{waiting} 개")
        self.stats_total_posts.setText(f"{total_posts} 개")

    def update_time(self):
        if hasattr(self, 'time_label'):
            self.time_label.setText(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def closeEvent(self, event):
        # 창 크기와 위치 저장
        self.save_window_geometry()
        
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self.cleanup_deleted_posts()
        
        # 스하리 워커들 정리
        for account_index, worker in self.sahari_workers.items():
            if worker and worker.isRunning():
                worker.stop()
        
        # 스하리 통계 저장
        self.save_sahari_stats()
        
        # 정상 종료 표시 (크래시 로그 저장 방지)
        crash_logger.mark_normal_exit()
        event.accept()

    def toggle_select_all_accounts(self):
        """계정 전체 선택/해제 토글 (위젯 안전성 강화)"""
        try:
            if not hasattr(self, 'account_table') or self.account_table is None:
                return
                
            if not hasattr(self, '_all_accounts_selected'):
                self._all_accounts_selected = False
            self._all_accounts_selected = not self._all_accounts_selected
            
            row_count = self.account_table.rowCount()
            for i in range(row_count):
                try:
                    container_widget = self.account_table.cellWidget(i, 0)
                    if container_widget:
                        checkbox = container_widget.findChild(QCheckBox)
                        if checkbox:  # isNull() 제거
                            checkbox.setChecked(self._all_accounts_selected)
                except (RuntimeError, AttributeError):
                    # 위젯이 삭제되었거나 접근할 수 없는 경우 무시
                    continue
            
            if hasattr(self, 'select_all_accounts_btn'):
                self.select_all_accounts_btn.setText("전체 해제" if self._all_accounts_selected else "전체 선택")
        except Exception as e:
            self.add_log(f"❌ 계정 전체 선택 토글 중 오류: {e}")

    def toggle_password_visibility(self):
        """비밀번호 보이기/숨기기 토글 (메모리 안전성 강화)"""
        try:
            self.password_visible = not getattr(self, 'password_visible', False)
            
            # 모든 비밀번호 입력란에 즉시 반영 (안전성 검증 추가)
            if hasattr(self, '_password_editors') and self._password_editors:
                editors_to_remove = []
                for key, editor in self._password_editors.items():
                    try:
                        if editor:  # 위젯이 존재하는지만 확인
                            editor.setEchoMode(QLineEdit.Normal if self.password_visible else QLineEdit.Password)
                        else:
                            editors_to_remove.append(key)
                    except RuntimeError:
                        # 위젯이 이미 삭제된 경우
                        editors_to_remove.append(key)
                
                # 삭제된 위젯 참조 정리
                for key in editors_to_remove:
                    del self._password_editors[key]
            
            if hasattr(self, 'toggle_password_btn'):
                self.toggle_password_btn.setText("비밀번호 숨기기" if self.password_visible else "비밀번호 보이기")
                
        except Exception as e:
            self.add_log(f"❌ 비밀번호 가시성 토글 중 오류: {e}")

    def launch_browser_for_account(self, account_index):
        account = self.accounts[account_index]
        username = account.get('username', f'account_{account_index}')
        proxy_ip = account.get('proxy_ip')
        proxy_port = account.get('proxy_port')

        self.add_log(f"🖥️ [{username}] 일반 크롬 런처 실행 중...")
        try:
            chrome_path = self.settings.get('chrome_path', r"C:/Program Files/Google/Chrome/Application/chrome.exe")
            profile_path = os.path.join(os.getcwd(), "chrome_profiles", username)
            os.makedirs(profile_path, exist_ok=True)
            chrome_args = [chrome_path, f'--user-data-dir={profile_path}']

            # 프록시 입력이 모두 공백이 아니고, 값이 있을 때만 프록시 적용
            if proxy_ip and proxy_port and proxy_ip.strip() and proxy_port.strip():
                chrome_args.append(f'--proxy-server=http://{proxy_ip}:{proxy_port}')
                self.add_log(f"   - 프록시 적용: {proxy_ip}:{proxy_port}")
                chrome_args.append("https://whatismyipaddress.com/")
            else:
                reply = QMessageBox.question(self, "프록시 미설정", "프록시가 올바르게 설정되지 않았습니다.\n기본IP로 접속할까요?", QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.No:
                    self.add_log(f"   - [취소] 프록시 미설정으로 실행하지 않음.")
                    return
                self.add_log("   - 프록시 없음. 직접 연결합니다.")
                chrome_args.append("https://whatismyipaddress.com/")

            import subprocess
            subprocess.Popen(chrome_args)
            self.add_log(f"✅ [{username}] 일반 크롬이 성공적으로 실행되었습니다.")
            if proxy_ip and proxy_port and proxy_ip.strip() and proxy_port.strip():
                self.add_log(f"   📍 IP 확인 페이지가 자동으로 열렸습니다. 프록시 IP: {proxy_ip}가 표시되는지 확인하세요.")
        except Exception as e:
            self.add_log(f"❌ [{username}] 크롬 실행 실패: {e}")
            QMessageBox.critical(self, "오류", f"크롬 실행 중 오류: {e}")

    def setup_styles(self):
        style = """
        QMainWindow { background-color: #f5f5f5; }
        QGroupBox { font-weight: bold; border: 2px solid #cccccc; border-radius: 5px; margin-top: 10px; padding-top: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        QPushButton { background-color: #2196F3; color: white; border: none; border-radius: 6px; padding: 6px 8px; font-weight: bold; text-align: center; }
        QPushButton#tableButton { min-height: 26px; max-height: 26px; font-size: 7pt; margin: 0px; padding: 0px; }
        QPushButton:hover { background-color: #1976D2; }
        QPushButton:pressed { background-color: #0D47A1; }
        QPushButton:disabled { background-color: #cccccc; color: #666666; }
        QTableWidget { background-color: white; border: 1px solid #ddd; border-radius: 4px; gridline-color: #bbb; show-decoration-selected: 0; }
        QTableWidget::item { padding: 0px; border-bottom: 1px solid #bbb; font-size: 7pt; height: 26px; }
        QTableWidget::item:selected { background-color: #E3F2FD; color: #1976D2; }
        QHeaderView::section { background-color: #f8f9fa; padding: 8px; border: 1px solid #dee2e6; font-weight: bold; }
        QTextEdit, QLineEdit { background-color: white; border: 1px solid #ddd; border-radius: 4px; padding: 8px; min-height: 25px; font-size: 8pt; }
        QTableWidget QLineEdit { min-height: 26px; max-height: 26px; font-size: 7pt; border: 1px solid #999; border-radius: 1px; margin: 0px; padding: 0px; }
        QTableWidget QPushButton { min-height: 26px; max-height: 26px; padding: 0px; font-size: 7pt; margin: 0px; }
        QTableWidget QPushButton#sahariButton { background-color: #FF9800; color: white; font-weight: bold; }
        QTableWidget QPushButton#sahariButton:hover { background-color: #F57C00; }
        QTableWidget QCheckBox { min-height: 26px; max-height: 26px; margin: 0px; }
        QProgressBar { border: 1px solid #ddd; border-radius: 4px; text-align: center; }
        QProgressBar::chunk { background-color: #4CAF50; border-radius: 3px; }
        QTabWidget::pane { border: 1px solid #ddd; border-radius: 4px; }
        QTabBar::tab { background-color: #f8f9fa; padding: 8px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
        QTabBar::tab:selected { background-color: white; border-bottom: 2px solid #2196F3; }
        """
        self.setStyleSheet(style)
        if hasattr(self, 'account_table'):
            self.account_table.verticalHeader().setDefaultSectionSize(26)
            for i in range(self.account_table.rowCount()):
                self.account_table.setRowHeight(i, 26)
        if hasattr(self, 'post_table'):
            self.post_table.verticalHeader().setDefaultSectionSize(30)
            for i in range(self.post_table.rowCount()):
                self.post_table.setRowHeight(i, 30)

    def load_settings(self):
        """settings.json 파일에서 설정을 불러옵니다."""
        try:
            with open('settings.json', 'r', encoding='utf-8') as f:
                self.settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.settings = {
                'repeat_interval': 0,
                'auto_delete_completed_posts': False,
                'chrome_path': r'C:/Program Files/Google/Chrome/Application/chrome.exe',
                'concurrent_limit': 1,
                'sahari_concurrent_limit': 1
            }
            self.save_settings()

    def save_settings(self):
        """현재 설정을 settings.json 파일에 저장합니다."""
        try:
            with open('settings.json', 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.add_log(f"❌ 설정 저장 실패: {e}")

    def upload_images(self):
        """이미지 파일들을 선택하고 Catbox.moe에 업로드합니다."""
        try:
            self.add_log("🔍 파일 선택 다이얼로그를 엽니다...")
            file_paths, _ = QFileDialog.getOpenFileNames(
                self, 
                "이미지 파일 선택", 
                "", 
                "이미지 파일 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;모든 파일 (*.*)"
            )
            
            if not file_paths:
                self.add_log("❌ 파일이 선택되지 않았습니다.")
                return
                
            self.add_log(f"📁 이미지 업로드 시작: {len(file_paths)}개 파일")
            
            # 업로드 진행
            uploaded_urls = []
            for i, file_path in enumerate(file_paths):
                self.add_log(f"   🔄 업로드 중 ({i+1}/{len(file_paths)}): {os.path.basename(file_path)}")
                self.add_log(f"      파일 경로: {file_path}")
                
                try:
                    self.add_log(f"      📤 {os.path.basename(file_path)} 업로드 중...")
                    
                    # catbox_uploader의 print 출력을 터미널에만 표시
                    url = catbox_uploader.upload_file(file_path)
                    uploaded_urls.append(url)
                    self.add_log(f"      ✅ 업로드 성공")
                        
                except Exception as e:
                    self.add_log(f"      ❌ 업로드 실패: {e}")
            
            self.add_log(f"📊 업로드 결과: 성공 {len(uploaded_urls)}개, 실패 {len(file_paths) - len(uploaded_urls)}개")
            
            if uploaded_urls:
                # 기존 URL과 새 URL을 합쳐서 쉼표로 구분
                current_urls = self.post_image_url_input.text().strip()
                if current_urls:
                    new_urls = current_urls + ", " + ", ".join(uploaded_urls)
                else:
                    new_urls = ", ".join(uploaded_urls)
                
                self.post_image_url_input.setText(new_urls)
                self.add_log(f"✅ {len(uploaded_urls)}개 이미지 URL이 입력칸에 추가되었습니다.")
            else:
                self.add_log("❌ 업로드된 이미지가 없습니다.")
                
        except Exception as e:
            self.add_log(f"❌ 이미지 업로드 중 오류 발생: {e}")
            self.add_log(f"   오류 타입: {type(e).__name__}")
            self.add_log(f"   오류 상세: {str(e)}")
            QMessageBox.critical(self, "오류", f"이미지 업로드 중 오류가 발생했습니다:\n{e}")

    def upload_video(self):
        """동영상 파일을 선택하고 Catbox.moe에 업로드합니다."""
        try:
            self.add_log("🔍 동영상 파일 선택 다이얼로그를 엽니다...")
            file_path, _ = QFileDialog.getOpenFileName(
                self, 
                "동영상 파일 선택", 
                "", 
                "동영상 파일 (*.mp4 *.avi *.mov *.wmv *.flv *.webm *.mkv);;모든 파일 (*.*)"
            )
            
            if not file_path:
                self.add_log("❌ 동영상 파일이 선택되지 않았습니다.")
                return
                
            self.add_log(f"📁 동영상 업로드 시작: {os.path.basename(file_path)}")
            self.add_log(f"   파일 경로: {file_path}")
            
            # 업로드 진행
            try:
                self.add_log(f"   📤 동영상 업로드 중...")
                url = catbox_uploader.upload_file(file_path)
                self.post_video_url_input.setText(url)
                self.add_log(f"   ✅ 동영상 업로드 성공")
                
            except Exception as e:
                self.add_log(f"   ❌ 동영상 업로드 실패: {e}")
                self.add_log(f"   오류 상세: {type(e).__name__}: {str(e)}")
                QMessageBox.critical(self, "오류", f"동영상 업로드 중 오류가 발생했습니다:\n{e}")
                
        except Exception as e:
            self.add_log(f"❌ 동영상 업로드 중 오류 발생: {e}")
            self.add_log(f"   오류 타입: {type(e).__name__}")
            self.add_log(f"   오류 상세: {str(e)}")
            QMessageBox.critical(self, "오류", f"동영상 업로드 중 오류가 발생했습니다:\n{e}")

    def import_accounts_from_excel(self):
        """엑셀 파일로 계정 목록을 업로드 (A:아이디, B:비밀번호, C:api id, D:토큰, E:프록시 IP, F:포트, 2번째 행부터)"""
        file_path, _ = QFileDialog.getOpenFileName(self, "엑셀 파일 선택", "", "Excel Files (*.xlsx *.xls)")
        if not file_path:
            return
        try:
            import pandas as pd
            df = pd.read_excel(file_path, header=None, skiprows=1, dtype=str).fillna("")
            new_accounts = []
            for _, row in df.iterrows():
                new_accounts.append({
                    'checked': True,
                    'username': row.get(0, ''),
                    'password': row.get(1, ''),
                    'api_id': row.get(2, ''),
                    'token': row.get(3, ''),
                    'proxy_ip': row.get(4, ''),
                    'proxy_port': row.get(5, ''),
                    'status': '대기중'
                })
            # 50개 미만이면 나머지는 빈칸으로
            while len(new_accounts) < 50:
                new_accounts.append({
                    'checked': True,
                    'username': '',
                    'password': '',
                    'api_id': '',
                    'token': '',
                    'proxy_ip': '',
                    'proxy_port': '',
                    'status': '대기중'
                })
            # 50개 초과면 자름
            new_accounts = new_accounts[:50]
            self.accounts = new_accounts
            self.save_data_to_file('accounts.json', self.accounts)
            self.account_table.setRowCount(len(self.accounts))
            self.load_account_data()
            self.add_log(f"📥 엑셀에서 {len(df)}개 계정 정보를 불러왔습니다.")
        except Exception as e:
            QMessageBox.critical(self, "오류", f"엑셀 파일 읽기 실패: {e}")

    def export_accounts_to_excel(self):
        """전체 계정 목록을 엑셀 파일로 저장"""
        file_path, _ = QFileDialog.getSaveFileName(self, "엑셀 파일 저장", f"accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx", "Excel Files (*.xlsx)")
        if not file_path:
            return
        try:
            import pandas as pd
            
            # 헤더 행 추가
            data = [['아이디', '비밀번호', 'API ID', '토큰', '프록시 IP', '포트']]
            
            # 계정 데이터 추가 (빈 계정 제외)
            for account in self.accounts:
                if account.get('username', '').strip():  # 아이디가 있는 계정만
                    data.append([
                        account.get('username', ''),
                        account.get('password', ''),
                        account.get('api_id', ''),
                        account.get('token', ''),
                        account.get('proxy_ip', ''),
                        account.get('proxy_port', '')
                    ])
            
            df = pd.DataFrame(data[1:], columns=data[0])  # 헤더 제외하고 데이터프레임 생성
            df.to_excel(file_path, index=False)
            
            exported_count = len(df)
            self.add_log(f"📤 {exported_count}개 계정 정보를 엑셀 파일로 저장했습니다: {os.path.basename(file_path)}")
            QMessageBox.information(self, "저장 완료", f"{exported_count}개 계정 정보가 저장되었습니다.\n{file_path}")
            
        except Exception as e:
            QMessageBox.critical(self, "오류", f"엑셀 파일 저장 실패: {e}")
            self.add_log(f"❌ 엑셀 저장 실패: {e}")

    def delete_selected_accounts(self):
        """체크된 계정만 삭제"""
        indices_to_delete = [i for i, account in enumerate(self.accounts) if account.get('checked', False)]
        if not indices_to_delete:
            QMessageBox.information(self, "알림", "선택된 계정이 없습니다.")
            return
        
        # 삭제할 계정 정보 표시
        account_names = [self.accounts[i].get('username', f'계정{i+1}') for i in indices_to_delete]
        account_list = '\n'.join([f"• {name}" for name in account_names])
        
        if QMessageBox.question(self, "선택계정 삭제 확인", 
                              f"선택된 {len(indices_to_delete)}개 계정을 삭제하시겠습니까?\n\n{account_list}") == QMessageBox.Yes:
            
            # 역순으로 삭제 (인덱스 변화 방지)
            for idx in sorted(indices_to_delete, reverse=True):
                del self.accounts[idx]
            
            # 50개 유지 (빈 계정으로 채움)
            while len(self.accounts) < 50:
                self.accounts.append({
                    'checked': True,
                    'username': '',
                    'password': '',
                    'api_id': '',
                    'token': '',
                    'proxy_ip': '',
                    'proxy_port': '',
                    'status': '대기중'
                })
            
            self.save_data_to_file('accounts.json', self.accounts)
            self.account_table.setRowCount(len(self.accounts))
            self.load_account_data()
            self.add_log(f"🗑️ 선택된 {len(indices_to_delete)}개 계정을 삭제했습니다.")
            QMessageBox.information(self, "삭제 완료", f"{len(indices_to_delete)}개 계정이 삭제되었습니다.")

    def auto_get_api_id_from_token(self):
        """선택된 계정들의 토큰을 사용해서 API ID를 자동으로 조회하고 입력합니다."""
        # 선택된 계정들 찾기
        selected_accounts = []
        for i, account in enumerate(self.accounts):
            if account.get('checked', False):
                token = account.get('token', '').strip()
                if token:  # 토큰이 있는 계정만
                    selected_accounts.append((i, account))
        
        if not selected_accounts:
            QMessageBox.warning(self, "경고", "토큰이 있는 선택된 계정이 없습니다.")
            return
        
        self.add_log(f"🔄 {len(selected_accounts)}개 계정의 API ID를 자동으로 조회합니다...")
        
        success_count = 0
        for i, account in selected_accounts:
            try:
                token = account.get('token', '').strip()
                username = account.get('username', f'계정{i+1}')
                
                self.add_log(f"   - {username} 계정 API ID 조회 중...")
                
                # 토큰으로 사용자 정보 조회
                user_id, info = self.get_user_id_from_token(token)
                
                if user_id:
                    # API ID 필드 업데이트
                    self.accounts[i]['api_id'] = user_id
                    self.save_data_to_file('accounts.json', self.accounts)
                    
                    # UI 테이블 업데이트 (컨테이너에서 QLineEdit 찾아서 반영)
                    api_id_container = self.account_table.cellWidget(i, 3)  # API ID 열
                    if api_id_container:
                        api_id_editor = api_id_container.findChild(QLineEdit)
                        if api_id_editor:
                            api_id_editor.setText(user_id)
                    
                    self.add_log(f"   ✅ {username} API ID 조회 성공: {user_id}")
                    success_count += 1
                else:
                    self.add_log(f"   ❌ {username} API ID 조회 실패: {info}")
                
            except Exception as e:
                username = account.get('username', f'계정{i+1}')
                self.add_log(f"   ❌ {username} API ID 조회 중 오류: {e}")
        
        self.add_log(f"📊 API ID 자동 조회 완료: 성공 {success_count}개, 실패 {len(selected_accounts) - success_count}개")
        
        if success_count > 0:
            QMessageBox.information(self, "완료", f"API ID 자동 조회가 완료되었습니다.\n성공: {success_count}개, 실패: {len(selected_accounts) - success_count}개")
        else:
            QMessageBox.warning(self, "실패", "API ID 조회에 실패했습니다. 토큰이 유효한지 확인해주세요.")

    def get_user_id_from_token(self, access_token):
        """토큰으로 사용자 ID 추출"""
        try:
            url = f"https://graph.threads.net/v1.0/me"
            params = {
                "fields": "id,username,name,threads_profile_picture_url",
                "access_token": access_token
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                user_info = response.json()
                return user_info.get("id"), user_info
            else:
                return None, response.text
        except Exception as e:
            return None, str(e)

    def update_post_status(self, index, status_text):
        """게시글 상태 UI 업데이트 (인덱스 범위 검증 강화)"""
        try:
            if not (0 <= index < len(self.posts)):
                self.add_log(f"⚠️ 잘못된 게시글 인덱스: {index} (최대: {len(self.posts)-1})")
                return
                
            if not hasattr(self, 'post_table') or self.post_table is None:
                return
                
            self.posts[index]['status'] = status_text
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignCenter)
            if status_text == "완료":
                status_item.setBackground(QColor(200, 255, 200))
            elif status_text == "실패":
                status_item.setBackground(QColor(255, 200, 200))
            elif "진행" in status_text:
                status_item.setBackground(QColor(255, 255, 200))
            
            # UI 스레드에서만 테이블 업데이트
            if index < self.post_table.rowCount():
                self.post_table.setItem(index, 6, status_item)
        except Exception as e:
            self.add_log(f"❌ 게시글 상태 업데이트 중 오류: {e}")

    def toggle_select_all_posts(self):
        """전체 선택/해제 토글"""
        if not hasattr(self, '_all_posts_selected'):
            self._all_posts_selected = False
        self._all_posts_selected = not self._all_posts_selected
        for i in range(self.post_table.rowCount()):
            container_widget = self.post_table.cellWidget(i, 0)
            if container_widget:
                checkbox = container_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(self._all_posts_selected)
        self.select_all_posts_btn.setText("전체 해제" if self._all_posts_selected else "전체 선택")

    def open_repeat_dialog(self, post_index):
        post = self.posts[post_index]
        dlg = QDialog(self)
        dlg.setWindowTitle("반복 설정")
        dlg.setMinimumWidth(300)
        layout = QVBoxLayout(dlg)
        
        # 현재 반복 상태 표시
        current_repeat = post.get('repeat_count', 1)
        if current_repeat == 1:
            status_label = QLabel("현재: 반복 없음 (1회만 실행)")
        else:
            status_label = QLabel(f"현재: {current_repeat}회 반복")
        status_label.setStyleSheet("font-weight: bold; color: #2E86AB;")
        layout.addWidget(status_label)
        
        # 구분선
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)
        
        # 반복 횟수 설정
        label = QLabel("반복 횟수를 설정하세요:")
        layout.addWidget(label)
        
        spin = QSpinBox()
        spin.setRange(1, 100)
        spin.setValue(current_repeat)
        spin.setSuffix("회 반복")
        layout.addWidget(spin)
        
        # 반복 취소 옵션
        cancel_repeat_checkbox = QCheckBox("반복 취소 (1회만 실행)")
        cancel_repeat_checkbox.setChecked(current_repeat == 1)
        layout.addWidget(cancel_repeat_checkbox)
        
        # 체크박스와 스핀박스 연동
        def on_checkbox_changed(state):
            if state == Qt.Checked:
                spin.setValue(1)
                spin.setEnabled(False)
            else:
                spin.setEnabled(True)
        
        def on_spin_changed(value):
            if value == 1:
                cancel_repeat_checkbox.setChecked(True)
            else:
                cancel_repeat_checkbox.setChecked(False)
        
        cancel_repeat_checkbox.stateChanged.connect(on_checkbox_changed)
        spin.valueChanged.connect(on_spin_changed)
        
        # 버튼
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("저장")
        btns.button(QDialogButtonBox.Cancel).setText("취소")
        layout.addWidget(btns)
        
        def save():
            if cancel_repeat_checkbox.isChecked():
                repeat_count = 1
                # 반복취소 시 상태를 '대기중'으로 변경하고 UI에도 즉시 반영
                self.posts[post_index]['status'] = '대기중'
                self.update_post_status(post_index, '대기중')
            else:
                repeat_count = spin.value()
            self.posts[post_index]['repeat_count'] = repeat_count
            self.posts[post_index]['repeat_progress'] = 0
            self.save_data_to_file('posts.json', self.posts)
            self.load_post_data()
            dlg.accept()
        
        btns.accepted.connect(save)
        btns.rejected.connect(dlg.reject)
        dlg.exec_()

    def moveEvent(self, event):
        x = self.x()
        y = self.y()
        if y < 0:
            self.move(x, 0)
        super().moveEvent(event)

    # 스하리 관련 메서드들
    def start_sahari_for_account(self, account_index):
        """개별 계정 스하리 시작"""
        if account_index >= len(self.accounts):
            self.add_sahari_log(f"❌ 잘못된 계정 인덱스: {account_index}")
            return
        
        account = self.accounts[account_index]
        username = account.get('username', f'account_{account_index}')
        
        # 이미 실행 중인지 확인
        if account_index in self.sahari_workers and self.sahari_workers[account_index].isRunning():
            self.add_sahari_log(f"⚠️ [{username}] 스하리 작업이 이미 실행 중입니다.")
            return
        
        # --- 반복 실행/1회 실행 선택 팝업 추가 ---
        from PyQt5.QtWidgets import QMessageBox
        repeat_hour = self.sahari_repeat_hour_input.value() if hasattr(self, 'sahari_repeat_hour_input') else 0
        repeat_min = self.sahari_repeat_min_input.value() if hasattr(self, 'sahari_repeat_min_input') else 0
        repeat_none = self.sahari_repeat_none_checkbox.isChecked() if hasattr(self, 'sahari_repeat_none_checkbox') else False
        repeat_interval_sec = (repeat_hour * 60 + repeat_min) * 60 if not repeat_none else 0
        
        msg = QMessageBox(self)
        msg.setWindowTitle("스하리 실행 방식 선택")
        msg.setText(f"[{username}] 계정의 스하리 실행 방식을 선택하세요.")
        repeat_btn = msg.addButton("반복 실행", QMessageBox.AcceptRole)
        once_btn = msg.addButton("1회 실행", QMessageBox.DestructiveRole)
        cancel_btn = msg.addButton("취소", QMessageBox.RejectRole)
        msg.setIcon(QMessageBox.Question)
        msg.exec_()
        if msg.clickedButton() == cancel_btn:
            self.add_sahari_log(f"[{username}] 스하리 실행이 취소되었습니다.")
            return
        is_repeat = (msg.clickedButton() == repeat_btn)
        
        # 설정 검증
        search_query = self.sahari_search_input.text().strip()
        if not search_query:
            QMessageBox.warning(self, "경고", "검색어를 입력해주세요.")
            return
        
        # 프록시 정보 확인 및 로그
        proxy_ip = account.get('proxy_ip', '')
        proxy_port = account.get('proxy_port', '')
        proxy_server = f"{proxy_ip}:{proxy_port}" if proxy_ip and proxy_port else ''
        
        if proxy_server:
            self.add_sahari_log(f"🌐 [{username}] 프록시 설정 확인: {proxy_server}")
        else:
            self.add_sahari_log(f"🌐 [{username}] 프록시 미설정 - 직접 연결")
        
        # --- 범위값 랜덤 추출 ---
        import random
        delay_min = self.sahari_delay_min_input.value()
        delay_max = self.sahari_delay_max_input.value()
        delay_seconds = random.randint(delay_min, delay_max)
        like_min = self.sahari_like_min_input.value()
        like_max = self.sahari_like_max_input.value()
        like_range = f"{like_min}-{like_max}"
        repost_min = self.sahari_repost_min_input.value()
        repost_max = self.sahari_repost_max_input.value()
        repost_range = f"{repost_min}-{repost_max}"
        comment_min = self.sahari_comment_min_input.value()
        comment_max = self.sahari_comment_max_input.value()
        comment_range = f"{comment_min}-{comment_max}"
        
        def run_single_sahari():
            worker = SahariWorker(
                search_query=search_query,
                delay_seconds=delay_seconds,
                manual_comments=self.sahari_comments_input.toPlainText(),
                email=account.get('username', ''),
                password=account.get('password', ''),
                follow_count=self.sahari_follow_count_input.value(),
                like_range=like_range,
                repost_range=repost_range,
                comment_range=comment_range,
                proxy_server=proxy_server
            )
            # 시그널 연결
            worker.progress.connect(lambda msg: self.add_sahari_log(f"[{username}] {msg}"))
            worker.finished.connect(lambda: self.on_sahari_finished(account_index))
            # 통계 업데이트 시그널 연결
            worker.stats_updated = lambda action_type: self.update_sahari_stats(account_index, action_type)
            self.sahari_workers[account_index] = worker
            worker.start()
            self.add_sahari_log(f"🚀 [{username}] 스하리 작업을 시작합니다.")
            self.update_sahari_status(account_index, "실행 중", "0%", "스하리 작업")
        
        def run_repeat_sahari():
            # 반복 실행 로직 (QTimer 활용)
            from PyQt5.QtCore import QTimer
            self._sahari_repeat_stop_single = False
            # 워커 객체를 외부에서 참조할 수 있도록
            current_worker = {'worker': None}
            def start_and_schedule(start_index=1):
                if getattr(self, '_sahari_repeat_stop_single', False):
                    self.add_sahari_log(f"[{username}] 반복 실행이 중지되었습니다.")
                    return
                # 이전 워커의 시그널 연결 해제
                if current_worker['worker']:
                    try:
                        current_worker['worker'].finished.disconnect()
                    except Exception:
                        pass
                    try:
                        current_worker['worker'].retry_needed.disconnect()
                    except Exception:
                        pass
                # 새 워커 생성 및 실행
                worker = SahariWorker(
                    search_query=search_query,
                    delay_seconds=delay_seconds,
                    manual_comments=self.sahari_comments_input.toPlainText(),
                    email=account.get('username', ''),
                    password=account.get('password', ''),
                    follow_count=self.sahari_follow_count_input.value(),
                    like_range=like_range,
                    repost_range=repost_range,
                    comment_range=comment_range,
                    proxy_server=proxy_server,
                    start_index=start_index
                )
                current_worker['worker'] = worker
                worker.progress.connect(lambda msg: self.add_sahari_log(f"[{username}] {msg}"))
                # 통계 업데이트 시그널 연결
                worker.stats_updated = lambda action_type: self.update_sahari_stats(account_index, action_type)
                # finished 시그널: 정상 완료 시에만 반복 타이머로 넘어감
                def on_finished():
                    if getattr(self, '_sahari_repeat_stop_single', False):
                        self.add_sahari_log(f"[{username}] 반복 실행이 중지되었습니다.")
                        return
                    if repeat_interval_sec > 0:
                        mins = repeat_interval_sec // 60
                        secs = repeat_interval_sec % 60
                        self.add_sahari_log(f"[스하리] [{username}] 작업 완료, {mins}분 {secs}초 후 반복 실행 대기...")
                        self._sahari_repeat_timer_single = QTimer(self)
                        self._sahari_repeat_timer_single.setSingleShot(True)
                        def restart():
                            self.add_sahari_log(f"[스하리] [{username}] 반복 대기 종료, 스하리 작업 재시작!")
                            start_and_schedule(start_index=1)  # 반복 라운드마다 1부터 시작
                        self._sahari_repeat_timer_single.timeout.connect(restart)
                        self._sahari_repeat_timer_single.start(repeat_interval_sec * 1000)
                worker.finished.connect(on_finished)
                # retry_needed 시그널: 오류 3회 초과 시 남은 작업만 즉시 재시작
                def on_retry_needed(next_index):
                    self.add_sahari_log(f"[{username}] 오류로 인해 남은 작업({self.sahari_follow_count_input.value() - next_index + 1}개)만 재시작합니다.")
                    # finished 연결 해제(중복 방지)
                    try:
                        worker.finished.disconnect(on_finished)
                    except Exception:
                        pass
                    start_and_schedule(start_index=next_index)
                worker.retry_needed.connect(on_retry_needed)
                self.sahari_workers[account_index] = worker
                worker.start()
                self.add_sahari_log(f"🚀 [{username}] 스하리 작업을 시작합니다.")
                self.update_sahari_status(account_index, "실행 중", "0%", "스하리 작업")
            start_and_schedule(start_index=1)
        
        if is_repeat:
            run_repeat_sahari()
        else:
            run_single_sahari()

    def start_all_sahari(self):
        """전체 선택된 계정 스하리 시작"""
        # 선택된 계정들 찾기
        selected_accounts = []
        for i, account in enumerate(self.accounts):
            if account.get('checked', False):
                selected_accounts.append(i)
        
        if not selected_accounts:
            QMessageBox.warning(self, "경고", "선택된 계정이 없습니다.")
            return
        
        # 설정 검증
        search_query = self.sahari_search_input.text().strip()
        if not search_query:
            QMessageBox.warning(self, "경고", "검색어를 입력해주세요.")
            return
        
        self.sahari_is_running = True
        self.sahari_start_all_btn.setEnabled(False)
        self.sahari_stop_all_btn.setEnabled(True)
        
        self.add_sahari_log(f"🚀 선택된 {len(selected_accounts)}개 계정으로 전체 스하리 작업을 시작합니다.")
        
        # --- 스하리 동시 실행 그룹화 ---
        sahari_limit = self.settings.get('sahari_concurrent_limit', 1)
        account_groups = [selected_accounts[j:j+sahari_limit] for j in range(0, len(selected_accounts), sahari_limit)]
        
        # 반복간격(분) 계산
        repeat_hour = self.sahari_repeat_hour_input.value() if hasattr(self, 'sahari_repeat_hour_input') else 0
        repeat_min = self.sahari_repeat_min_input.value() if hasattr(self, 'sahari_repeat_min_input') else 0
        repeat_none = self.sahari_repeat_none_checkbox.isChecked() if hasattr(self, 'sahari_repeat_none_checkbox') else False
        repeat_interval_sec = (repeat_hour * 60 + repeat_min) * 60 if not repeat_none else 0
        
        def run_next_group(group_idx=0):
            if group_idx >= len(account_groups):
                self.sahari_is_running = False
                self.sahari_start_all_btn.setEnabled(True)
                self.sahari_stop_all_btn.setEnabled(True)  # 반복 대기 중에도 중지 가능하게!
                self.add_sahari_log("🏁 전체 스하리 작업이 모두 완료되었습니다.")
                # --- 반복 실행 로직 추가 ---
                if repeat_interval_sec > 0 and getattr(self, '_sahari_repeat_stop', False) is not True:
                    mins = repeat_interval_sec // 60
                    secs = repeat_interval_sec % 60
                    self.add_sahari_log(f"[스하리] 모든 계정 작업 완료, {mins}분 {secs}초 후 반복 실행 대기...")
                    def restart_sahari():
                        if getattr(self, '_sahari_repeat_stop', False) is not True:
                            self.add_sahari_log(f"[스하리] 반복 대기 종료, 전체 스하리 작업 재시작!")
                            self.start_all_sahari()
                    # QTimer 사용 (메인스레드 안전)
                    from PyQt5.QtCore import QTimer
                    self._sahari_repeat_stop = False
                    self._sahari_repeat_timer = QTimer(self)
                    self._sahari_repeat_timer.setSingleShot(True)
                    self._sahari_repeat_timer.timeout.connect(restart_sahari)
                    self._sahari_repeat_timer.start(repeat_interval_sec * 1000)
                return
            group = account_groups[group_idx]
            running = []
            finished = [False] * len(group)
            def on_finished(idx):
                finished[idx] = True
                if all(finished):
                    run_next_group(group_idx + 1)
            for idx, account_index in enumerate(group):
                def make_cb(i):
                    return lambda: on_finished(i)
                self.start_sahari_for_account(account_index)
                # 기존 워커에 finished 시그널 연결
                if account_index in self.sahari_workers:
                    self.sahari_workers[account_index].finished.connect(make_cb(idx))
        # 반복 중지 플래그 초기화
        self._sahari_repeat_stop = False
        run_next_group(0)

    def stop_all_sahari(self):
        """전체 스하리 작업 중지"""
        self.sahari_is_running = False
        self.sahari_start_all_btn.setEnabled(True)
        self.sahari_stop_all_btn.setEnabled(False)
        
        # 모든 스하리 워커 중지
        for account_index, worker in self.sahari_workers.items():
            if worker and worker.isRunning():
                worker.stop()
        
        # 반복 대기 중이면 타이머도 중지
        if hasattr(self, '_sahari_repeat_timer') and self._sahari_repeat_timer:
            self._sahari_repeat_stop = True
            self._sahari_repeat_timer.stop()
            self.add_sahari_log("[스하리] 반복 대기 중지됨.")
        
        # 개별 반복 실행 중지도 처리
        if hasattr(self, '_sahari_repeat_timer_single') and self._sahari_repeat_timer_single:
            self._sahari_repeat_stop_single = True
            self._sahari_repeat_timer_single.stop()
            self.add_sahari_log("[스하리] 개별 반복 대기 중지됨.")
        self.add_sahari_log("⏹️ 모든 스하리 작업을 중지합니다.")

    def on_sahari_finished(self, account_index):
        """스하리 작업 완료 처리"""
        if account_index in self.sahari_workers:
            del self.sahari_workers[account_index]
        
        if account_index < len(self.accounts):
            username = self.accounts[account_index].get('username', f'account_{account_index}')
            self.add_sahari_log(f"✅ [{username}] 스하리 작업이 완료되었습니다.")
            self.update_sahari_status(account_index, "완료", "100%", "")

    def update_sahari_status(self, account_index, status, progress, action):
        """스하리 상태 테이블 업데이트 (새로운 구조)"""
        try:
            if 0 <= account_index < len(self.accounts):
                # username을 사용하도록 수정 (email 대신)
                account_username = self.accounts[account_index].get('username', f'account_{account_index}')
                
                # 기존 행 찾기
                row_index = -1
                for i in range(self.sahari_status_table.rowCount()):
                    if self.sahari_status_table.item(i, 0) and self.sahari_status_table.item(i, 0).text() == account_username:
                        row_index = i
                        break
                
                # 새 행 추가
                if row_index == -1:
                    row_index = self.sahari_status_table.rowCount()
                    self.sahari_status_table.insertRow(row_index)
                    self.sahari_status_table.setItem(row_index, 0, QTableWidgetItem(account_username))
                
                # 통계 데이터 가져오기 (username을 키로 사용)
                stats = self.sahari_stats.get(account_username, {
                    'follows': 0, 'likes': 0, 'reposts': 0, 'comments': 0
                })
                
                # 각 컬럼 업데이트 (새로운 구조)
                self.sahari_status_table.setItem(row_index, 1, QTableWidgetItem(str(stats['follows'])))
                self.sahari_status_table.setItem(row_index, 2, QTableWidgetItem(str(stats['likes'])))
                self.sahari_status_table.setItem(row_index, 3, QTableWidgetItem(str(stats['reposts'])))
                self.sahari_status_table.setItem(row_index, 4, QTableWidgetItem(str(stats['comments'])))
                self.sahari_status_table.setItem(row_index, 5, QTableWidgetItem(status))
                self.sahari_status_table.setItem(row_index, 6, QTableWidgetItem(progress))
                self.sahari_status_table.setItem(row_index, 7, QTableWidgetItem(action))
                
                # 상태에 따른 색상 설정 (상태 컬럼: 5번)
                status_item = self.sahari_status_table.item(row_index, 5)
                if status == "완료":
                    status_item.setBackground(QColor(200, 255, 200))
                elif status == "실행 중":
                    status_item.setBackground(QColor(255, 255, 200))
                elif status == "실패":
                    status_item.setBackground(QColor(255, 200, 200))
                
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 상태 업데이트 실패: {e}")

    def save_sahari_config(self):
        """스하리 설정 수동 저장 (사용자 요청 시)"""
        try:
            config = {
                'search_query': self.sahari_search_input.text(),
                'delay_min': self.sahari_delay_min_input.value(),
                'delay_max': self.sahari_delay_max_input.value(),
                'manual_comments': self.sahari_comments_input.toPlainText(),
                'follow_count': self.sahari_follow_count_input.value(),
                'like_min': self.sahari_like_min_input.value(),
                'like_max': self.sahari_like_max_input.value(),
                'repost_min': self.sahari_repost_min_input.value(),
                'repost_max': self.sahari_repost_max_input.value(),
                'comment_min': self.sahari_comment_min_input.value(),
                'comment_max': self.sahari_comment_max_input.value(),
                'repeat_hour': self.sahari_repeat_hour_input.value(),
                'repeat_minute': self.sahari_repeat_min_input.value(),
                'repeat_none': self.sahari_repeat_none_checkbox.isChecked()
            }
            
            with open('sahari_config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            
            self.add_sahari_log("💾 스하리 설정이 수동 저장되었습니다.")
            QMessageBox.information(self, "저장 완료", "스하리 설정이 저장되었습니다.")
            
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 설정 수동 저장 실패: {e}")
            QMessageBox.critical(self, "저장 실패", f"스하리 설정 저장 중 오류가 발생했습니다:\n{e}")

    def load_sahari_config(self):
        """스하리 설정 불러오기"""
        try:
            if os.path.exists('sahari_config.json'):
                with open('sahari_config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                self.sahari_search_input.setText(config.get('search_query', ''))
                self.sahari_delay_min_input.setValue(config.get('delay_min', 1))
                self.sahari_delay_max_input.setValue(config.get('delay_max', 3))
                self.sahari_comments_input.setPlainText(config.get('manual_comments', ''))
                self.sahari_follow_count_input.setValue(config.get('follow_count', 5))
                self.sahari_like_min_input.setValue(config.get('like_min', 1))
                self.sahari_like_max_input.setValue(config.get('like_max', 3))
                self.sahari_repost_min_input.setValue(config.get('repost_min', 0))
                self.sahari_repost_max_input.setValue(config.get('repost_max', 1))
                self.sahari_comment_min_input.setValue(config.get('comment_min', 0))
                self.sahari_comment_max_input.setValue(config.get('comment_max', 2))
                self.sahari_repeat_hour_input.setValue(config.get('repeat_hour', 0))
                self.sahari_repeat_min_input.setValue(config.get('repeat_minute', 0))
                self.sahari_repeat_none_checkbox.setChecked(config.get('repeat_none', False))
                self.add_sahari_log("📂 스하리 설정을 불러왔습니다.")
            else:
                self.add_sahari_log("📝 기본 스하리 설정을 사용합니다.")
                
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 설정 불러오기 실패: {e}")
            QMessageBox.critical(self, "불러오기 실패", f"스하리 설정 불러오기 중 오류가 발생했습니다:\n{e}")

    def auto_save_sahari_config(self):
        """스하리 설정 자동 저장 (조용히 실행)"""
        try:
            config = {
                'search_query': self.sahari_search_input.text(),
                'delay_min': self.sahari_delay_min_input.value(),
                'delay_max': self.sahari_delay_max_input.value(),
                'manual_comments': self.sahari_comments_input.toPlainText(),
                'follow_count': self.sahari_follow_count_input.value(),
                'like_min': self.sahari_like_min_input.value(),
                'like_max': self.sahari_like_max_input.value(),
                'repost_min': self.sahari_repost_min_input.value(),
                'repost_max': self.sahari_repost_max_input.value(),
                'comment_min': self.sahari_comment_min_input.value(),
                'comment_max': self.sahari_comment_max_input.value(),
                'repeat_hour': self.sahari_repeat_hour_input.value(),
                'repeat_minute': self.sahari_repeat_min_input.value(),
                'repeat_none': self.sahari_repeat_none_checkbox.isChecked()
            }
            
            with open('sahari_config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            
            # 자동 저장은 조용히 실행 (로그만 기록)
            #self.add_sahari_log("💾 스하리 설정이 자동 저장되었습니다.")
            
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 설정 자동 저장 실패: {e}")

    def reset_sahari_status(self):
        """스하리 실행 상태 초기화"""
        try:
            reply = QMessageBox.question(
                self, 
                "초기화 확인", 
                "스하리 실행 상태를 모두 초기화하시겠습니까?\n(누적된 작업 수가 모두 삭제됩니다)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # 테이블 초기화
                self.sahari_status_table.setRowCount(0)
                
                # 누적 통계 초기화
                self.sahari_stats = {}
                
                # 파일에서도 삭제
                if os.path.exists('sahari_stats.json'):
                    os.remove('sahari_stats.json')
                
                self.add_sahari_log("🗑️ 스하리 실행 상태가 초기화되었습니다.")
                QMessageBox.information(self, "초기화 완료", "스하리 실행 상태가 초기화되었습니다.")
                
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 상태 초기화 실패: {e}")
            QMessageBox.critical(self, "초기화 실패", f"스하리 상태 초기화 중 오류가 발생했습니다:\n{e}")

    def load_sahari_stats(self):
        """스하리 누적 통계 불러오기"""
        try:
            if os.path.exists('sahari_stats.json'):
                with open('sahari_stats.json', 'r', encoding='utf-8') as f:
                    self.sahari_stats = json.load(f)
                self.add_sahari_log("📂 스하리 누적 통계를 불러왔습니다.")
                
                # 기존 통계를 테이블에 표시
                self.display_existing_sahari_stats()
            else:
                self.sahari_stats = {}
                self.add_sahari_log("📝 새로운 스하리 누적 통계를 시작합니다.")
                
        except Exception as e:
            self.sahari_stats = {}
            self.add_sahari_log(f"❌ 스하리 통계 불러오기 실패: {e}")

    def display_existing_sahari_stats(self):
        """기존 스하리 통계를 테이블에 표시"""
        try:
            for account_username, stats in self.sahari_stats.items():
                # 해당 계정의 인덱스 찾기
                account_index = -1
                for i, account in enumerate(self.accounts):
                    if account.get('username', f'account_{i}') == account_username:
                        account_index = i
                        break
                
                if account_index >= 0:
                    # 테이블에 행 추가
                    row = self.sahari_status_table.rowCount()
                    self.sahari_status_table.insertRow(row)
                    
                    # 데이터 설정
                    self.sahari_status_table.setItem(row, 0, QTableWidgetItem(account_username))
                    self.sahari_status_table.setItem(row, 1, QTableWidgetItem(str(stats.get('follows', 0))))
                    self.sahari_status_table.setItem(row, 2, QTableWidgetItem(str(stats.get('likes', 0))))
                    self.sahari_status_table.setItem(row, 3, QTableWidgetItem(str(stats.get('reposts', 0))))
                    self.sahari_status_table.setItem(row, 4, QTableWidgetItem(str(stats.get('comments', 0))))
                    self.sahari_status_table.setItem(row, 5, QTableWidgetItem("대기"))
                    self.sahari_status_table.setItem(row, 6, QTableWidgetItem("0%"))
                    self.sahari_status_table.setItem(row, 7, QTableWidgetItem(""))
                    
        except Exception as e:
            self.add_sahari_log(f"❌ 기존 스하리 통계 표시 실패: {e}")

    def save_sahari_stats(self):
        """스하리 누적 통계 저장"""
        try:
            with open('sahari_stats.json', 'w', encoding='utf-8') as f:
                json.dump(self.sahari_stats, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 통계 저장 실패: {e}")

    def update_sahari_stats(self, account_index, action_type):
        """스하리 작업 완료 시 통계 업데이트"""
        try:
            if 0 <= account_index < len(self.accounts):
                account_username = self.accounts[account_index].get('username', f'account_{account_index}')
                
                if account_username not in self.sahari_stats:
                    self.sahari_stats[account_username] = {
                        'follows': 0,
                        'likes': 0,
                        'reposts': 0,
                        'comments': 0
                    }
                
                # 해당 작업 카운터 증가
                if action_type in ['follow', '팔로우']:
                    self.sahari_stats[account_username]['follows'] += 1
                elif action_type in ['like', '좋아요']:
                    self.sahari_stats[account_username]['likes'] += 1
                elif action_type in ['repost', '리포스트']:
                    self.sahari_stats[account_username]['reposts'] += 1
                elif action_type in ['comment', '댓글']:
                    self.sahari_stats[account_username]['comments'] += 1
                
                # 통계 저장
                self.save_sahari_stats()
                
                # UI 업데이트
                self.update_sahari_status_display(account_index)
                
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 통계 업데이트 실패: {e}")



    def update_sahari_status_display(self, account_index):
        """스하리 상태 테이블 UI 업데이트"""
        try:
            if 0 <= account_index < len(self.accounts):
                account_username = self.accounts[account_index].get('username', f'account_{account_index}')
                
                # 테이블에서 해당 계정 행 찾기
                row_found = False
                for row in range(self.sahari_status_table.rowCount()):
                    if self.sahari_status_table.item(row, 0) and self.sahari_status_table.item(row, 0).text() == account_username:
                        row_found = True
                        break
                
                if not row_found:
                    # 새 행 추가
                    row = self.sahari_status_table.rowCount()
                    self.sahari_status_table.insertRow(row)
                    self.sahari_status_table.setItem(row, 0, QTableWidgetItem(account_username))
                
                # 통계 데이터 가져오기
                stats = self.sahari_stats.get(account_username, {
                    'follows': 0, 'likes': 0, 'reposts': 0, 'comments': 0
                })
                
                # 각 컬럼 업데이트
                self.sahari_status_table.setItem(row, 1, QTableWidgetItem(str(stats['follows'])))
                self.sahari_status_table.setItem(row, 2, QTableWidgetItem(str(stats['likes'])))
                self.sahari_status_table.setItem(row, 3, QTableWidgetItem(str(stats['reposts'])))
                self.sahari_status_table.setItem(row, 4, QTableWidgetItem(str(stats['comments'])))
                
        except Exception as e:
            self.add_sahari_log(f"❌ 스하리 상태 표시 업데이트 실패: {e}")

    def update_sysinfo_and_check(self):
        """CPU/MEM 사용률 표시 및 경고 팝업 관리"""
        if not psutil:
            self.sysinfo_label.setText("psutil 미설치")
            return
        cpu = int(psutil.cpu_percent(interval=None))
        mem = int(psutil.virtual_memory().percent)
        self.sysinfo_label.setText(f"CPU 사용률: {cpu}%  메모리 사용률: {mem}%")
        cpu_over = cpu >= 80
        mem_over = mem >= 80
        # 팝업이 이미 떠 있는지 확인
        if (cpu_over or mem_over):
            msg = ""
            if cpu_over and mem_over:
                msg = ("현재 CPU와 메모리 사용률이 모두 80%를 초과했습니다.\n"
                       "동시 실행 수량을 줄이거나, 불필요한 프로그램을 종료해 주세요.")
            elif cpu_over:
                msg = ("현재 CPU 사용률이 80%를 초과했습니다.\n"
                       "동시 실행 수량을 줄이거나, 다른 프로그램을 종료해 주세요.")
            elif mem_over:
                msg = ("현재 메모리 사용률이 80%를 초과했습니다.\n"
                       "작업을 줄이거나, 불필요한 프로그램을 종료해 주세요.")
            # 이미 팝업이 떠 있으면 메시지만 업데이트
            if self._sysinfo_popup and self._sysinfo_popup.isVisible():
                self._sysinfo_popup.setText(msg)
            else:
                self._sysinfo_popup = QMessageBox(self)
                self._sysinfo_popup.setWindowTitle("시스템 자원 경고")
                self._sysinfo_popup.setIcon(QMessageBox.Warning)
                self._sysinfo_popup.setText(msg)
                self._sysinfo_popup.setStandardButtons(QMessageBox.Ok)
                self._sysinfo_popup.button(QMessageBox.Ok).setText("확인")
                self._sysinfo_popup.setModal(False)
                self._sysinfo_popup.setWindowModality(Qt.NonModal)
                self._sysinfo_popup.show()
        # 팝업이 떠 있는데 둘 다 80% 미만이면 닫기(자동 닫기 X, 확인 버튼으로만 닫음)
        # (아무 동작 안 함)

    def _on_sahari_repeat_none_changed(self, state):
        checked = (state == Qt.Checked)
        self.sahari_repeat_hour_input.setEnabled(not checked)
        self.sahari_repeat_min_input.setEnabled(not checked)
        if checked:
            self.sahari_repeat_hour_input.setValue(0)
            self.sahari_repeat_min_input.setValue(0)
        self.auto_save_sahari_config()

    def stop_all_sahari_force(self):
        """스하리중지 버튼: 모든 스하리 작업(개별/전체/반복) 즉시 중지"""
        # 기존 stop_all_sahari와 동일하게 동작
        self.stop_all_sahari()
        self.add_sahari_log("⏹️ [스하리중지] 버튼으로 모든 스하리 작업을 즉시 중지했습니다.")


class TokenWorker(QThread):
    short_token_ready = pyqtSignal(str)
    long_token_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    def __init__(self, step, auth_code=None, short_token=None, client_id=None, client_secret=None, redirect_uri=None):
        super().__init__()
        self.step = step
        self.auth_code = auth_code
        self.short_token = short_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
    def run(self):
        try:
            if self.step == 'short':
                self.get_short_token()
            elif self.step == 'long':
                self.get_long_token()
        except Exception as e:
            self.error_occurred.emit(f"오류 발생: {str(e)}")
    def get_short_token(self):
        CLIENT_ID = self.client_id
        CLIENT_SECRET = self.client_secret
        REDIRECT_URI = self.redirect_uri
        auth_code = self.auth_code
        if auth_code.endswith("#_"):
            auth_code = auth_code[:-2]
        url = "https://graph.threads.net/oauth/access_token"
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": auth_code
        }
        response = requests.post(url, data=data)
        result = response.json()
        if 'access_token' in result:
            self.short_token_ready.emit(result['access_token'])
        else:
            self.error_occurred.emit(f"Short Token 발급 실패: {result}")
    def get_long_token(self):
        short_token = self.short_token
        if short_token.endswith("#_"):
            short_token = short_token[:-2]
        url = "https://graph.threads.net/access_token"
        params = {
            "grant_type": "th_exchange_token",
            "client_secret": self.client_secret,
            "access_token": short_token
        }
        response = requests.get(url, params=params)
        result = response.json()
        if 'access_token' in result:
            self.long_token_ready.emit(result['access_token'])
        else:
            self.error_occurred.emit(f"Long Token 발급 실패: {result}")

class ThreadsTokenGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🔑 Threads Token 자동 발급 프로그램")
        self.setGeometry(100, 100, 700, 600)
        self.config_file = "threads_config.json"
        self.setup_ui()
        self.setup_styles()
        self.load_config()
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        title_label = QLabel("🔑 Threads Token 자동 발급 프로그램")
        title_font = QFont("맑은 고딕", 16, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #2196F3; margin: 10px;")
        layout.addWidget(title_label)
        self.setup_app_config_group(layout)
        self.setup_step1_group(layout)
        self.setup_step2_group(layout)
        self.setup_result_area(layout)
        self.setup_bottom_buttons(layout)
    def setup_app_config_group(self, parent_layout):
        group = QGroupBox("⚙️ Threads 앱 정보 설정")
        group.setFont(QFont("맑은 고딕", 11, QFont.Bold))
        layout = QVBoxLayout(group)
        client_id_layout = QHBoxLayout()
        client_id_layout.addWidget(QLabel("CLIENT_ID:"))
        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText("Threads 앱의 CLIENT_ID를 입력하세요...")
        self.client_id_edit.textChanged.connect(self.auto_save_config)
        client_id_layout.addWidget(self.client_id_edit)
        layout.addLayout(client_id_layout)
        client_secret_layout = QHBoxLayout()
        client_secret_layout.addWidget(QLabel("CLIENT_SECRET:"))
        self.client_secret_edit = QLineEdit()
        self.client_secret_edit.setPlaceholderText("Threads 앱의 CLIENT_SECRET을 입력하세요...")
        self.client_secret_edit.setEchoMode(QLineEdit.Password)
        self.client_secret_edit.textChanged.connect(self.auto_save_config)
        client_secret_layout.addWidget(self.client_secret_edit)
        self.toggle_secret_btn = QPushButton("👁️")
        self.toggle_secret_btn.setFixedSize(40, 30)
        self.toggle_secret_btn.clicked.connect(self.toggle_secret_visibility)
        client_secret_layout.addWidget(self.toggle_secret_btn)
        layout.addLayout(client_secret_layout)
        redirect_uri_layout = QHBoxLayout()
        redirect_uri_layout.addWidget(QLabel("REDIRECT_URI:"))
        self.redirect_uri_edit = QLineEdit()
        self.redirect_uri_edit.setPlaceholderText("리다이렉트 URI를 입력하세요")
        self.redirect_uri_edit.textChanged.connect(self.auto_save_config)
        redirect_uri_layout.addWidget(self.redirect_uri_edit)
        layout.addLayout(redirect_uri_layout)
        parent_layout.addWidget(group)
    def setup_step1_group(self, parent_layout):
        group = QGroupBox("1단계: Authorization Code → Short Token")
        group.setFont(QFont("맑은 고딕", 11, QFont.Bold))
        layout = QVBoxLayout(group)
        auth_layout = QHBoxLayout()
        auth_layout.addWidget(QLabel("Authorization Code:"))
        self.auth_code_edit = QLineEdit()
        self.auth_code_edit.setPlaceholderText("Authorization Code 또는 전체 URL을 입력하세요...")
        self.auth_code_edit.textChanged.connect(self.extract_auth_code_from_url)
        auth_layout.addWidget(self.auth_code_edit)
        self.short_token_btn = QPushButton("Short Token 발급")
        self.short_token_btn.clicked.connect(self.get_short_token)
        auth_layout.addWidget(self.short_token_btn)
        layout.addLayout(auth_layout)
        short_layout = QHBoxLayout()
        short_layout.addWidget(QLabel("Short Token:"))
        self.short_token_edit = QLineEdit()
        self.short_token_edit.setPlaceholderText("Short Token이 여기에 표시됩니다...")
        self.short_token_edit.setReadOnly(True)
        short_layout.addWidget(self.short_token_edit)
        layout.addLayout(short_layout)
        parent_layout.addWidget(group)
    def setup_step2_group(self, parent_layout):
        group = QGroupBox("2단계: Short Token → Long Token")
        group.setFont(QFont("맑은 고딕", 11, QFont.Bold))
        layout = QVBoxLayout(group)
        short_input_layout = QHBoxLayout()
        short_input_layout.addWidget(QLabel("Short Token:"))
        self.short_token_manual_edit = QLineEdit()
        self.short_token_manual_edit.setPlaceholderText("Short Token은 자동으로 입력이 됩니다다...")
        short_input_layout.addWidget(self.short_token_manual_edit)
        layout.addLayout(short_input_layout)
        long_result_layout = QHBoxLayout()
        long_result_layout.addWidget(QLabel("Long Token:"))
        self.long_token_edit = QLineEdit()
        self.long_token_edit.setPlaceholderText("Long Token이 여기에 표시됩니다...")
        self.long_token_edit.setReadOnly(True)
        long_result_layout.addWidget(self.long_token_edit)
        self.copy_long_token_btn = QPushButton("📋 복사")
        self.copy_long_token_btn.clicked.connect(self.copy_long_token)
        long_result_layout.addWidget(self.copy_long_token_btn)
        layout.addLayout(long_result_layout)
        user_id_layout = QHBoxLayout()
        user_id_layout.addWidget(QLabel("사용자 ID:"))
        self.user_id_edit = QLineEdit()
        self.user_id_edit.setPlaceholderText("사용자 ID가 자동으로 추출됩니다...")
        self.user_id_edit.setReadOnly(True)
        user_id_layout.addWidget(self.user_id_edit)
        self.copy_user_id_btn = QPushButton("📋 복사")
        self.copy_user_id_btn.clicked.connect(self.copy_user_id)
        user_id_layout.addWidget(self.copy_user_id_btn)
        layout.addLayout(user_id_layout)
        parent_layout.addWidget(group)
    def setup_result_area(self, parent_layout):
        group = QGroupBox("📋 결과 출력")
        group.setFont(QFont("맑은 고딕", 11, QFont.Bold))
        layout = QVBoxLayout(group)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFont(QFont("Consolas", 9))
        self.result_text.setMaximumHeight(150)
        layout.addWidget(self.result_text)
        parent_layout.addWidget(group)
    def setup_bottom_buttons(self, parent_layout):
        button_layout = QHBoxLayout()
        clear_btn = QPushButton("🗑️ 모두 지우기")
        clear_btn.clicked.connect(self.clear_all)
        button_layout.addWidget(clear_btn)
        save_btn = QPushButton("💾 Long Token 저장")
        save_btn.clicked.connect(self.save_long_token)
        button_layout.addWidget(save_btn)
        button_layout.addStretch()
        parent_layout.addLayout(button_layout)
    def setup_styles(self):
        style = """
        QMainWindow { background-color: #f5f5f5; }
        QGroupBox { font-weight: bold; border: 2px solid #cccccc; border-radius: 8px; margin-top: 15px; padding-top: 15px; background-color: white; }
        QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 8px 0 8px; color: #2196F3; }
        QPushButton { background-color: #2196F3; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; font-size: 11px; }
        QPushButton:hover { background-color: #1976D2; }
        QPushButton:pressed { background-color: #0D47A1; }
        QPushButton:disabled { background-color: #cccccc; color: #666666; }
        QLineEdit { padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 10px; }
        QTextEdit { background-color: white; border: 1px solid #ddd; border-radius: 4px; font-family: Consolas, monospace; }
        """
        self.setStyleSheet(style)
    def toggle_secret_visibility(self):
        if self.client_secret_edit.echoMode() == QLineEdit.Password:
            self.client_secret_edit.setEchoMode(QLineEdit.Normal)
            self.toggle_secret_btn.setText("🙈")
        else:
            self.client_secret_edit.setEchoMode(QLineEdit.Password)
            self.toggle_secret_btn.setText("👁️")
    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.client_id_edit.setText(config.get('client_id', ''))
                    self.client_secret_edit.setText(config.get('client_secret', ''))
                    self.redirect_uri_edit.setText(config.get('redirect_uri', ''))
                    self.result_text.append("✅ 설정 파일을 불러왔습니다.")
            else:
                self.client_id_edit.setText("")
                self.client_secret_edit.setText("")
                self.redirect_uri_edit.setText("https://www.ktbaroshop.co.kr/")
                self.result_text.append("📝 기본값으로 설정되었습니다.")
                self.save_config()
        except Exception as e:
            self.result_text.append(f"❌ 설정 파일 로드 실패: {e}")
    def save_config(self):
        try:
            config = {
                'client_id': self.client_id_edit.text(),
                'client_secret': self.client_secret_edit.text(),
                'redirect_uri': self.redirect_uri_edit.text()
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.result_text.append(f"❌ 설정 파일 저장 실패: {e}")
    def auto_save_config(self):
        try:
            config = {
                'client_id': self.client_id_edit.text(),
                'client_secret': self.client_secret_edit.text(),
                'redirect_uri': self.redirect_uri_edit.text()
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            pass
    def get_short_token(self):
        client_id = self.client_id_edit.text().strip()
        client_secret = self.client_secret_edit.text().strip()
        redirect_uri = self.redirect_uri_edit.text().strip()
        auth_code = self.auth_code_edit.text().strip()
        if not client_id or not client_secret:
            QMessageBox.warning(self, "경고", "CLIENT_ID와 CLIENT_SECRET을 입력해주세요.")
            return
        if not redirect_uri:
            QMessageBox.warning(self, "경고", "리다이렉트 URI를 입력해주세요.")
            return
        if not auth_code:
            QMessageBox.warning(self, "경고", "Authorization Code를 입력해주세요.")
            return
        self.short_token_btn.setEnabled(False)
        self.short_token_btn.setText("자동 발급 중...")
        self.result_text.append("🚀 자동 토큰 발급 프로세스 시작...")
        self.result_text.append("🔄 1단계: Short Token 발급 중...")
        self.short_worker = TokenWorker('short', auth_code=auth_code, 
                                       client_id=client_id, client_secret=client_secret,
                                       redirect_uri=redirect_uri)
        self.short_worker.short_token_ready.connect(self.on_short_token_ready_auto)
        self.short_worker.error_occurred.connect(self.on_error_auto)
        self.short_worker.finished.connect(self.on_short_worker_finished)
        self.short_worker.start()

    def get_long_token(self):
        client_secret = self.client_secret_edit.text().strip()
        if not client_secret:
            QMessageBox.warning(self, "경고", "CLIENT_SECRET을 입력해주세요.")
            return
        short_token = self.short_token_edit.text().strip()
        if not short_token:
            short_token = self.short_token_manual_edit.text().strip()
        if not short_token:
            QMessageBox.warning(self, "경고", "Short Token을 입력해주세요.")
            return
        self.long_token_btn.setEnabled(False)
        self.long_token_btn.setText("발급 중...")
        self.result_text.append("🔄 Long Token 발급 중...")
        self.long_worker = TokenWorker('long', short_token=short_token, 
                                      client_secret=client_secret)
        self.long_worker.long_token_ready.connect(self.on_long_token_ready)
        self.long_worker.error_occurred.connect(self.on_error)
        self.long_worker.finished.connect(self.on_long_worker_finished)
        self.long_worker.start()

    def get_long_token_auto(self, short_token):
        client_secret = self.client_secret_edit.text().strip()
        if not client_secret:
            self.result_text.append("❌ CLIENT_SECRET이 설정되지 않았습니다.")
            QMessageBox.critical(self, "자동화 실패", "CLIENT_SECRET이 설정되지 않았습니다.")
            self.short_token_btn.setEnabled(True)
            self.short_token_btn.setText("Short Token 발급")
            return
        self.long_worker = TokenWorker('long', short_token=short_token, 
                                      client_secret=client_secret)
        self.long_worker.long_token_ready.connect(self.on_long_token_ready_auto)
        self.long_worker.error_occurred.connect(self.on_error_auto)
        self.long_worker.finished.connect(self.on_long_worker_finished_auto)
        self.long_worker.start()

    def on_short_token_ready(self, token):
        self.short_token_edit.setText(token)
        self.short_token_manual_edit.setText(token)
        self.result_text.append("✅ Short Token 발급 완료!")
        self.result_text.append(f"Token: {token}")

    def on_short_token_ready_auto(self, token):
        self.short_token_edit.setText(token)
        self.short_token_manual_edit.setText(token)
        self.result_text.append("✅ 1단계: Short Token 발급 완료!")
        self.result_text.append(f"Short Token: {token}")
        self.result_text.append("🔄 2단계: Long Token 발급 중...")
        self.get_long_token_auto(token)

    def on_long_token_ready(self, token):
        self.long_token_edit.setText(token)
        self.result_text.append("✅ Long Token 발급 완료!")
        self.result_text.append(f"Token: {token}")
        self.result_text.append("🔄 사용자 ID 자동 추출 중...")
        self.extract_user_id(token)

    def on_long_token_ready_auto(self, token):
        self.long_token_edit.setText(token)
        self.result_text.append("✅ 2단계: Long Token 발급 완료!")
        self.result_text.append(f"Long Token: {token}")
        self.result_text.append("🔄 3단계: 사용자 ID 자동 추출 중...")
        self.extract_user_id_auto(token)

    def on_error(self, error_msg):
        self.result_text.append(f"❌ {error_msg}")
        QMessageBox.critical(self, "오류", error_msg)

    def on_error_auto(self, error_msg):
        self.result_text.append(f"❌ {error_msg}")
        QMessageBox.critical(self, "자동화 실패", f"토큰 발급 프로세스가 실패했습니다:\n{error_msg}")
        self.short_token_btn.setEnabled(True)
        self.short_token_btn.setText("Short Token 발급")

    def on_short_worker_finished(self):
        self.short_token_btn.setEnabled(True)
        self.short_token_btn.setText("Short Token 발급")

    def on_long_worker_finished(self):
        pass

    def on_long_worker_finished_auto(self):
        pass

    def clear_all(self):
        self.auth_code_edit.clear()
        self.short_token_edit.clear()
        self.short_token_manual_edit.clear()
        self.long_token_edit.clear()
        self.user_id_edit.clear()
        self.redirect_uri_edit.clear()
        self.result_text.clear()
        self.result_text.append("🗑️ 모든 입력란을 지웠습니다.")

    def extract_auth_code_from_url(self):
        text = self.auth_code_edit.text().strip()
        if text.startswith(('http://', 'https://')):
            try:
                parsed_url = urlparse(text)
                query_params = parse_qs(parsed_url.query)
                if 'code' in query_params:
                    code = query_params['code'][0]
                    if code.endswith('#_'):
                        code = code[:-2]
                    self.auth_code_edit.setText(code)
                    self.result_text.append("✅ URL에서 Authorization Code를 자동으로 추출했습니다.")
                    return
            except Exception as e:
                self.result_text.append(f"❌ URL 파싱 실패: {e}")
        elif 'code=' in text:
            try:
                match = re.search(r'code=([^&\s#?]+)', text)
                if match:
                    code = match.group(1)
                    if code.endswith('#_'):
                        code = code[:-2]
                    self.auth_code_edit.setText(code)
                    self.result_text.append("✅ URL에서 Authorization Code를 자동으로 추출했습니다.")
                    return
            except Exception as e:
                self.result_text.append(f"❌ 정규식 추출 실패: {e}")

    def get_user_id_from_token(self, access_token):
        try:
            url = f"https://graph.threads.net/v1.0/me"
            params = {
                "fields": "id,username,name,threads_profile_picture_url",
                "access_token": access_token
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                user_info = response.json()
                return user_info.get("id"), user_info
            else:
                return None, response.text
        except Exception as e:
            return None, str(e)

    def extract_user_id(self, access_token):
        user_id, info = self.get_user_id_from_token(access_token)
        if user_id:
            self.user_id_edit.setText(user_id)
            self.result_text.append(f"✅ 사용자 ID 추출 완료: {user_id}")
            self.result_text.append(f"사용자 정보: {json.dumps(info, indent=2, ensure_ascii=False)}")
        else:
            self.user_id_edit.setText("")
            self.result_text.append(f"❌ 사용자 ID 추출 실패: {info}")

    def extract_user_id_auto(self, access_token):
        user_id, info = self.get_user_id_from_token(access_token)
        if user_id:
            self.user_id_edit.setText(user_id)
            self.result_text.append(f"✅ 3단계: 사용자 ID 추출 완료!")
            self.result_text.append(f"사용자 ID: {user_id}")
            self.result_text.append(f"사용자 정보: {json.dumps(info, indent=2, ensure_ascii=False)}")
            self.result_text.append("🎉 모든 토큰 발급 프로세스가 완료되었습니다!")
            self.short_token_btn.setEnabled(True)
            self.short_token_btn.setText("Short Token 발급")
            QMessageBox.information(self, "자동화 완료", 
                                  f"모든 토큰 발급이 성공적으로 완료되었습니다!\n\n"
                                  f"• Short Token: 발급 완료\n"
                                  f"• Long Token: 발급 완료\n"
                                  f"• 사용자 ID: {user_id}")
        else:
            self.user_id_edit.setText("")
            self.result_text.append(f"❌ 3단계: 사용자 ID 추출 실패: {info}")
            QMessageBox.critical(self, "자동화 실패", f"사용자 ID 추출에 실패했습니다:\n{info}")
            self.short_token_btn.setEnabled(True)
            self.short_token_btn.setText("Short Token 발급")

    def copy_long_token(self):
        long_token = self.long_token_edit.text().strip()
        if long_token:
            clipboard = QApplication.clipboard()
            clipboard.setText(long_token, mode=QClipboard.Clipboard)
            QMessageBox.information(self, "복사 완료", "Long Token이 클립보드에 복사되었습니다!")
        else:
            QMessageBox.warning(self, "복사 오류", "먼저 Long Token을 발급하세요.")

    def copy_user_id(self):
        user_id = self.user_id_edit.text().strip()
        if user_id:
            clipboard = QApplication.clipboard()
            clipboard.setText(user_id, mode=QClipboard.Clipboard)
            QMessageBox.information(self, "복사 완료", "사용자 ID가 클립보드에 복사되었습니다!")
        else:
            QMessageBox.warning(self, "복사 오류", "먼저 사용자 ID를 추출하세요.")

    def save_long_token(self):
        long_token = self.long_token_edit.text().strip()
        if long_token:
            try:
                with open("long_token.txt", "w") as f:
                    f.write(long_token)
                QMessageBox.information(self, "저장 완료", "Long Token이 'long_token.txt' 파일에 저장되었습니다.")
            except Exception as e:
                QMessageBox.critical(self, "저장 실패", f"파일 저장 중 오류: {e}")
        else:
            QMessageBox.warning(self, "경고", "저장할 Long Token이 없습니다.")


def main():
    """메인 실행 함수"""
    try:
        app = QApplication(sys.argv)
        font = QFont("맑은 고딕", 8)
        app.setFont(font)
        window = MultiAccountGUI()
        window.show()
        window.add_log("🚀 무한 스레드 프로그램 v2.8 (스하리 기능 포함)")
        
        # 앱 실행
        exit_code = app.exec_()
        
        # 정상 종료 시 크래시 로그 저장 방지
        crash_logger.mark_normal_exit()
        sys.exit(exit_code)
        
    except Exception as e:
        # 예외 발생 시 크래시 로그에 기록하고 저장
        crash_logger.add_log(f"❌ 메인 함수에서 예외 발생: {e}")
        crash_logger.add_log(f"예외 타입: {type(e).__name__}")
        import traceback
        crash_logger.add_log(f"스택 트레이스:\n{traceback.format_exc()}")
        crash_logger.save_crash_log()
        print(f"❌ 프로그램 실행 중 오류 발생: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()