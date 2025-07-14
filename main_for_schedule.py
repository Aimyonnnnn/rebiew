import pathlib
from pathlib import Path
import sys
import time
import hashlib
import re
import random
import json
import os
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtWidgets import QMessageBox, QTabWidget, QWidget, QFileDialog
from PyQt5.QtCore import QObject, pyqtSignal
from playwright.sync_api import Playwright, sync_playwright, Page, BrowserContext
import random

# 상수 정의
THREADS_URL = "https://www.threads.com/?hl=ko"
CONFIG_FILE = "threads_config.json"

# 세션 저장 디렉토리
SESSION_STORE = {}

def load_config():
    """설정 파일 로드"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_config(config):
    """설정 파일 저장"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"설정 저장 실패: {e}")

def sanitize_folder_name(name):
    """폴더명에서 사용할 수 없는 문자 제거"""
    return re.sub(r'[<>:"/\\|?*]', '_', str(name))

def generate_account_hash(email, password):
    """계정 정보로부터 해시 생성"""
    account_string = f"{email}_{password}"
    return hashlib.md5(account_string.encode()).hexdigest()

def get_session_dir(email, password):
    """기존 크롬 프로필 사용"""
    # 기존 크롬 프로필 경로 사용
    chrome_profile_path = Path("chrome_profiles/fnlunasea5@gmail.com")
    
    if chrome_profile_path.exists():
        print(f"📁 기존 크롬 프로필 사용: {chrome_profile_path}")
        return str(chrome_profile_path)
    else:
        print(f"❌ 크롬 프로필을 찾을 수 없음: {chrome_profile_path}")
        # 폴백: 기존 세션 폴더 사용
        account_hash = generate_account_hash(email, password)
        session_dir = Path(f"sessions/{sanitize_folder_name(email)}_{account_hash}")
        
        # 기존 세션인지 확인
        if session_dir.exists():
            print(f"📁 기존 세션 재사용: {session_dir}")
        else:
            print(f"🆕 새 세션 생성: {session_dir}")
        
        session_dir.mkdir(parents=True, exist_ok=True)
        return str(session_dir)

def is_login_required(page):
    """로그인이 필요한지 확인"""
    try:
        print("🔍 로그인 상태 확인 시작...")
        
        # 현재 URL 확인
        current_url = page.url
        print(f"📍 현재 URL: {current_url}")
        
        # 페이지 로딩 5초 대기
        print("⏳ 페이지 로딩 대기 중... (5초)")
        page.wait_for_timeout(5000)
        
        # 최대 10번 시도 (총 20초)
        for attempt in range(10):
            print(f"🔄 시도 {attempt + 1}/10")
            
            # 1. 추천 텍스트 찾기
            recommend_element = page.locator('span:has-text("추천")').first
            if recommend_element.is_visible():
                print("✅ '추천' 텍스트 발견 - 로그인됨")
                return False  # 로그인됨
            
            # 2. 로그인 링크 2개 확인
            login_link1 = page.locator('a[href="/login?show_choice_screen=false"]:has-text("사용자 이름으로 로그인")').first
            login_link2 = page.locator('a[href="/login?show_choice_screen=false"]:has-text("로그인")').first
            
            if login_link1.is_visible() or login_link2.is_visible():
                if login_link1.is_visible():
                    print("❌ '사용자 이름으로 로그인' 링크 발견 - 로그인 필요")
                if login_link2.is_visible():
                    print("❌ '로그인' 링크 발견 - 로그인 필요")
                return True  # 로그인 필요
            
            # 3. 둘 다 없으면 2초 대기 후 재시도
            if attempt < 9:  # 마지막 시도가 아니면
                print("⏳ 추천과 로그인 링크 모두 없음 - 2초 후 재시도...")
                page.wait_for_timeout(2000)
        
        # 10번 시도 후에도 불명확하면 기본적으로 로그인 필요로 판단
        print("⚠️ 10번 시도 후에도 로그인 상태 불명확 - 기본적으로 로그인 필요로 판단")
        return True
        
    except Exception as e:
        print(f"❌ 로그인 상태 확인 중 오류: {e}")
        # 오류 발생 시 안전하게 로그인 필요로 판단
        return True

def perform_login(page, email, password):
    """Threads 로그인 수행"""
    try:
        # 페이지 로딩 대기
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(2000)  # 추가 대기
        
        # 이메일 입력 (실제 Threads 로그인 페이지 요소)
        email_input = page.locator('input[placeholder="사용자 이름, 전화번호 또는 이메일 주소"]').first
        email_input.fill(email)
        page.wait_for_timeout(1000)
        
        # 비밀번호 입력 (실제 Threads 로그인 페이지 요소)
        password_input = page.locator('input[placeholder="비밀번호"]').first
        password_input.fill(password)
        page.wait_for_timeout(1000)
        
        # 로그인 버튼 클릭 (실제 Threads 로그인 페이지 요소)
        login_button = page.locator('div[role="button"]:has-text("로그인")').first
        login_button.click()
        
        # 로그인 완료 대기
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(5000)  # 추가 대기
        
    except Exception as e:
        raise Exception(f"로그인 실패: {str(e)}")

def launch_user_context(playwright, email, password, proxy_server=None, proxy_username=None, proxy_password=None):
    """사용자 브라우저 컨텍스트 생성"""
    session_dir = get_session_dir(email, password)
    
    context_args = {
        'user_data_dir': session_dir,
        'headless': False,
        'args': ['--profile-directory=Default'],
        'ignore_https_errors': True
    }
    
    if proxy_server and proxy_server.strip():
        proxy_config = {'server': proxy_server.strip()}
        
        if proxy_username and proxy_username.strip():
            proxy_config['username'] = proxy_username.strip()
        
        if proxy_password and proxy_password.strip():
            proxy_config['password'] = proxy_password.strip()
        
        context_args['proxy'] = proxy_config
    
    return playwright.chromium.launch_persistent_context(**context_args)

def get_random_comment(comments_text):
    """댓글 목록에서 랜덤 선택"""
    if not comments_text:
        return ""
    
    comment_list = [line.strip() for line in comments_text.split('\n') if line.strip()]
    return random.choice(comment_list) if comment_list else ""

def parse_range(range_str):
    """범위 문자열을 파싱하여 최소값과 최대값 반환"""
    try:
        if '~' in range_str:
            min_val, max_val = map(int, range_str.split('~'))
            return min_val, max_val
        else:
            val = int(range_str)
            return val, val
    except:
        return 1, 1  # 기본값

class Worker(QObject):
    """백그라운드 작업 스레드"""
    finished = pyqtSignal()
    progress = pyqtSignal(str)
    
    DELAY_JITTER = 0.5
    
    def __init__(self, search_query: str, delay_seconds: int, manual_comments: str, email: str, password: str, follow_count: int, like_range: str, repost_range: str, comment_range: str, proxy_server: str = '', proxy_username: str = '', proxy_password: str = ''):
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
    
    def apply_delay(self):
        """지연 시간 적용"""
        delay_time = random.uniform(
            max(0.1, self.delay_seconds - self.DELAY_JITTER),
            self.delay_seconds + self.DELAY_JITTER
        )
        time.sleep(delay_time)
    
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
            
            # Threads 메인 페이지로 이동 (유동적 확인)
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
            page.wait_for_timeout(2000)
            
            self.progress.emit('Scrolling done, collecting elements...')
            
            # XPath로 요소 수집
            elements = page.locator('xpath=//*[@id="barcelona-page-layout"]/div/div/div[2]/div[1]/div[1]/div/div[2]/div/div/div[1]/div')
            
            count = 1
            follow_done = 0
            
            # 범위 파싱
            like_min, like_max = parse_range(self.like_range)
            repost_min, repost_max = parse_range(self.repost_range)
            comment_min, comment_max = parse_range(self.comment_range)
            
            while count <= self.follow_count:
                page.wait_for_timeout(1000)
                
                # 요소 스크롤 및 사용자명 추출
                elements.nth(count - 1).scroll_into_view_if_needed()
                username = elements.nth(count - 1).get_by_role('link').nth(0).all_inner_texts()[0]
                
                self.progress.emit(f'Processing user: {username}')
                
                # 사용자 페이지에서 팔로우
                user_page = context.new_page()
                user_page.goto(f'https://www.threads.com/@{username}')
                user_page.wait_for_timeout(3000)
                
                follow_button = user_page.get_by_role('button', name='팔로우').nth(0)
                if follow_button.is_visible():
                    follow_button.click()
                    follow_done += 1
                    self.progress.emit(f'Followed {username} ({follow_done}/{self.follow_count})')
                    self.apply_delay()
                
                user_page.close()
                page.wait_for_timeout(1000)
                
                # 좋아요 버튼 클릭 (각 팔로워당 랜덤 개수)
                like_count_for_user = random.randint(like_min, like_max)
                self.progress.emit(f'좋아요 {like_count_for_user}개 수행 예정')
                
                for like_idx in range(like_count_for_user):
                    if elements.nth(count - 1).get_by_role('button').filter(has_text='좋아요 취소').is_visible():
                        self.progress.emit(f'이미 좋아요됨 - 건너뜀')
                        break
                    else:
                        like_buttons = elements.nth(count - 1).get_by_role('button').filter(has_text='좋아요')
                        if like_buttons.count() == 1:
                            like_buttons.first.click()
                        else:
                            like_buttons.nth(0).click()
                        
                        self.progress.emit(f'Liked post ({like_idx + 1}/{like_count_for_user})')
                        self.apply_delay()
                
                # 리포스트 버튼 클릭 (각 팔로워당 랜덤 개수)
                repost_count_for_user = random.randint(repost_min, repost_max)
                if repost_count_for_user > 0:
                    self.progress.emit(f'리포스트 {repost_count_for_user}개 수행 예정')
                    
                    for repost_idx in range(repost_count_for_user):
                        elements.nth(count - 1).get_by_role('button').filter(has_text='리포스트').click()
                        page.wait_for_timeout(100)
                        page.get_by_role('button', name='리포스트 리포스트').click()
                        self.progress.emit(f'Reposted ({repost_idx + 1}/{repost_count_for_user})')
                        self.apply_delay()
                else:
                    self.progress.emit(f'리포스트 0개 - 건너뜀')
                
                # 댓글 작성 (각 팔로워당 랜덤 개수)
                comment_count_for_user = random.randint(comment_min, comment_max)
                if comment_count_for_user > 0:
                    self.progress.emit(f'댓글 {comment_count_for_user}개 작성 예정')
                    
                    for comment_idx in range(comment_count_for_user):
                        comment = get_random_comment(self.manual_comments)
                        if not comment:
                            self.progress.emit('No valid random comment available. Skipping comment.')
                            break
                        
                        # 답글 버튼 클릭
                        elements.nth(count - 1).get_by_role('button').filter(has_text='답글').click()
                        page.wait_for_timeout(500)
                        
                        # 텍스트박스 찾기
                        inline_textbox = page.get_by_role('textbox', name='텍스트 필드가 비어 있습니다. 입력하여 새 게시물을 작성해보세요')
                        modal_textbox = page.get_by_role('textbox', name='텍스트 필드가 비어 있습니다. 입력하여 새 게시물을 작성해보세요')
                        
                        # 인라인 방식 댓글 입력
                        if inline_textbox.count() > 1:
                            inline_textbox.nth(count - 1).fill(comment)
                            self.progress.emit(f"인라인 방식으로 댓글 입력: '{comment}'")
                            
                            if elements.nth(count - 1).get_by_role('button').filter(has_text='답글').count() > 1:
                                post_button = elements.nth(count - 1).get_by_role('button', name='답글').nth(2)
                            else:
                                post_button = elements.nth(count - 1).get_by_role('button', name='게시')
                        else:
                            # 모달 방식 댓글 입력
                            modal_textbox.first.fill(comment)
                            self.progress.emit(f"모달 방식으로 댓글 입력: '{comment}'")
                            
                            if page.get_by_role('button', name='답글').count() > 1:
                                post_button = elements.nth(count - 1).get_by_role('button', name='답글').nth(1)
                            else:
                                post_button = page.get_by_role('button', name='게시')
                        
                        post_button.click()
                        self.progress.emit(f"Replied with '{comment}' ({comment_idx + 1}/{comment_count_for_user})")
                        self.apply_delay()
                else:
                    self.progress.emit(f'댓글 0개 - 건너뜀')
                
                count += 1
                self.apply_delay()
            
            context.close()
            
        except Exception as e:
            if self.proxy_server:
                self.progress.emit(f'❌ 프록시 연결 실패: {str(e)}')
            else:
                self.progress.emit(f'❌ 브라우저 시작 실패: {str(e)}')
        except Exception as e:
            self.progress.emit(f'❌ 자동 로그인 실패: {str(e)}')
            context.close()
    
    def run(self):
        """메인 작업 실행"""
        self.progress.emit('Starting Playwright automation...')
        
        try:
            with sync_playwright() as playwright:
                self.run_playwright(playwright)
            
            self.progress.emit('Automation completed successfully.')
            
        except Exception as e:
            self.progress.emit(f'Error occurred: {str(e)}')
        finally:
            self.finished.emit()

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Threads 자동화 도구")
        self.setGeometry(100, 100, 800, 600)
        
        # UI 초기화
        self.init_ui()
        
        # 타이머 설정
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.start_automation)
        
        # 스레드 및 워커 초기화
        self.thread = None
        self.worker = None
    
    def init_ui(self):
        """UI 초기화"""
        layout = QtWidgets.QVBoxLayout()
        
        # 탭 위젯 생성
        self.tab_widget = QTabWidget()
        
        # 로그인 탭
        login_tab = QtWidgets.QWidget()
        login_layout = QtWidgets.QVBoxLayout()
        
        # 로그인 그룹
        login_group = QtWidgets.QGroupBox("로그인 정보")
        login_group_layout = QtWidgets.QGridLayout()
        
        # 이메일
        login_group_layout.addWidget(QtWidgets.QLabel("이메일:"), 0, 0)
        self.email_edit = QtWidgets.QLineEdit()
        login_group_layout.addWidget(self.email_edit, 0, 1)
        
        # 비밀번호
        login_group_layout.addWidget(QtWidgets.QLabel("비밀번호:"), 1, 0)
        self.password_edit = QtWidgets.QLineEdit()
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        login_group_layout.addWidget(self.password_edit, 1, 1)
        
        # 설정 저장/로드 버튼
        save_load_layout = QtWidgets.QHBoxLayout()
        self.save_config_button = QtWidgets.QPushButton("설정 저장")
        self.save_config_button.clicked.connect(self.save_current_config)
        self.load_config_button = QtWidgets.QPushButton("설정 불러오기")
        self.load_config_button.clicked.connect(self.load_current_config)
        save_load_layout.addWidget(self.save_config_button)
        save_load_layout.addWidget(self.load_config_button)
        login_group_layout.addLayout(save_load_layout, 2, 0, 1, 2)
        
        login_group.setLayout(login_group_layout)
        login_layout.addWidget(login_group)
        
        # 프록시 그룹
        proxy_group = QtWidgets.QGroupBox("프록시 설정 (선택사항)")
        proxy_group_layout = QtWidgets.QGridLayout()
        
        # 프록시 서버
        proxy_group_layout.addWidget(QtWidgets.QLabel("프록시 서버:"), 0, 0)
        self.proxy_server_edit = QtWidgets.QLineEdit()
        self.proxy_server_edit.setPlaceholderText("예: 127.0.0.1:8080")
        proxy_group_layout.addWidget(self.proxy_server_edit, 0, 1)
        
        # 프록시 사용자명
        proxy_group_layout.addWidget(QtWidgets.QLabel("프록시 사용자명:"), 1, 0)
        self.proxy_username_edit = QtWidgets.QLineEdit()
        proxy_group_layout.addWidget(self.proxy_username_edit, 1, 1)
        
        # 프록시 비밀번호
        proxy_group_layout.addWidget(QtWidgets.QLabel("프록시 비밀번호:"), 2, 0)
        self.proxy_password_edit = QtWidgets.QLineEdit()
        self.proxy_password_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        proxy_group_layout.addWidget(self.proxy_password_edit, 2, 1)
        
        # 프록시 테스트 버튼
        self.proxy_test_button = QtWidgets.QPushButton("연결 및 로그인 테스트")
        self.proxy_test_button.clicked.connect(self.test_connection_and_login)
        proxy_group_layout.addWidget(self.proxy_test_button, 3, 0, 1, 2)
        
        proxy_group.setLayout(proxy_group_layout)
        login_layout.addWidget(proxy_group)
        
        login_tab.setLayout(login_layout)
        self.tab_widget.addTab(login_tab, "로그인")
        
        # 자동화 탭
        automation_tab = QtWidgets.QWidget()
        automation_layout = QtWidgets.QVBoxLayout()
        
        # 자동화 설정 그룹
        automation_group = QtWidgets.QGroupBox("자동화 설정")
        automation_group_layout = QtWidgets.QGridLayout()
        
        # 검색어
        automation_group_layout.addWidget(QtWidgets.QLabel("검색어:"), 0, 0)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setEnabled(False)
        automation_group_layout.addWidget(self.search_edit, 0, 1)
        
        # 지연 시간
        automation_group_layout.addWidget(QtWidgets.QLabel("지연 시간(초):"), 1, 0)
        self.delay_edit = QtWidgets.QLineEdit("1")
        self.delay_edit.setEnabled(False)
        automation_group_layout.addWidget(self.delay_edit, 1, 1)
        
        # 수동 댓글
        automation_group_layout.addWidget(QtWidgets.QLabel("수동 댓글:"), 2, 0)
        self.manual_comments_edit = QtWidgets.QTextEdit()
        self.manual_comments_edit.setMaximumHeight(100)
        self.manual_comments_edit.setEnabled(False)
        automation_group_layout.addWidget(self.manual_comments_edit, 2, 1)
        
        # 작업 개수 설정 (팔로워 기준)
        automation_group_layout.addWidget(QtWidgets.QLabel("팔로우 개수:"), 3, 0)
        self.follow_count_edit = QtWidgets.QLineEdit("10")
        self.follow_count_edit.setEnabled(False)
        automation_group_layout.addWidget(self.follow_count_edit, 3, 1)
        
        automation_group_layout.addWidget(QtWidgets.QLabel("좋아요 범위 (예: 1~5):"), 4, 0)
        self.like_range_edit = QtWidgets.QLineEdit("1~5")
        self.like_range_edit.setEnabled(False)
        automation_group_layout.addWidget(self.like_range_edit, 4, 1)
        
        automation_group_layout.addWidget(QtWidgets.QLabel("리포스트 범위 (예: 0~2):"), 5, 0)
        self.repost_range_edit = QtWidgets.QLineEdit("0~2")
        self.repost_range_edit.setEnabled(False)
        automation_group_layout.addWidget(self.repost_range_edit, 5, 1)
        
        automation_group_layout.addWidget(QtWidgets.QLabel("댓글 범위 (예: 1~3):"), 6, 0)
        self.comment_range_edit = QtWidgets.QLineEdit("1~3")
        self.comment_range_edit.setEnabled(False)
        automation_group_layout.addWidget(self.comment_range_edit, 6, 1)
        
        # 실행 간격
        automation_group_layout.addWidget(QtWidgets.QLabel("실행 간격:"), 7, 0)
        self.interval_combo = QtWidgets.QComboBox()
        self.interval_combo.addItems(["1", "2", "3", "6", "12", "24"])
        self.interval_combo.setEnabled(False)
        automation_group_layout.addWidget(self.interval_combo, 7, 1)
        
        # 실행 버튼
        self.run_button = QtWidgets.QPushButton("자동화 시작")
        self.run_button.setEnabled(False)
        automation_group_layout.addWidget(self.run_button, 8, 0, 1, 2)
        
        automation_group.setLayout(automation_group_layout)
        automation_layout.addWidget(automation_group)
        
        # 로그 박스 (더 크게)
        log_group = QtWidgets.QGroupBox("실행 로그")
        log_layout = QtWidgets.QVBoxLayout()
        
        self.log_box = QtWidgets.QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(300)  # 최소 높이 설정
        self.log_box.setMaximumHeight(400)  # 최대 높이 설정
        self.log_box.setStyleSheet("""
            QTextEdit {
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 11px;
            }
        """)
        
        # 로그 제어 버튼
        log_control_layout = QtWidgets.QHBoxLayout()
        self.clear_log_button = QtWidgets.QPushButton("로그 지우기")
        self.clear_log_button.clicked.connect(self.clear_log)
        self.save_log_button = QtWidgets.QPushButton("로그 저장")
        self.save_log_button.clicked.connect(self.save_log)
        log_control_layout.addWidget(self.clear_log_button)
        log_control_layout.addWidget(self.save_log_button)
        log_control_layout.addStretch()
        
        log_layout.addWidget(self.log_box)
        log_layout.addLayout(log_control_layout)
        log_group.setLayout(log_layout)
        automation_layout.addWidget(log_group)
        
        automation_tab.setLayout(automation_layout)
        self.tab_widget.addTab(automation_tab, "자동화")
        
        layout.addWidget(self.tab_widget)
        self.setLayout(layout)
        
        # 버튼 연결
        self.run_button.clicked.connect(self.start_automation)
        
        # 입력 필드 변경 시 자동 저장 연결
        self.email_edit.textChanged.connect(self.auto_save_on_change)
        self.password_edit.textChanged.connect(self.auto_save_on_change)
        self.proxy_server_edit.textChanged.connect(self.auto_save_on_change)
        self.proxy_username_edit.textChanged.connect(self.auto_save_on_change)
        self.proxy_password_edit.textChanged.connect(self.auto_save_on_change)
        self.search_edit.textChanged.connect(self.auto_save_on_change)
        self.delay_edit.textChanged.connect(self.auto_save_on_change)
        self.manual_comments_edit.textChanged.connect(self.auto_save_on_change)
        self.follow_count_edit.textChanged.connect(self.auto_save_on_change)
        self.like_range_edit.textChanged.connect(self.auto_save_on_change)
        self.repost_range_edit.textChanged.connect(self.auto_save_on_change)
        self.comment_range_edit.textChanged.connect(self.auto_save_on_change)
        self.interval_combo.currentTextChanged.connect(self.auto_save_on_change)
        
        # 초기 설정 로드
        self.load_saved_config()
        
        # 초기 메시지
        self.log_box.append("🔧 먼저 '연결 및 로그인 테스트'를 완료해주세요!")
        self.log_box.append("💾 설정 저장/불러오기 기능을 사용할 수 있습니다.")
    
    def test_connection_and_login(self):
        """연결 및 로그인 테스트"""
        proxy_server = self.proxy_server_edit.text().strip()
        proxy_username = self.proxy_username_edit.text().strip()
        proxy_password = self.proxy_password_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.password_edit.text().strip()
        
        if not email or not password:
            self.log_box.append('⚠️ 로그인 정보를 입력해주세요!')
            return
        
        self.proxy_test_button.setEnabled(False)
        
        # 테스트 워커 스레드 생성
        self.test_thread = QtCore.QThread()
        self.test_worker = TestWorker(email, password, proxy_server, proxy_username, proxy_password)
        self.test_worker.moveToThread(self.test_thread)
        
        # 시그널 연결
        self.test_thread.started.connect(self.test_worker.run)
        self.test_worker.finished.connect(self.on_test_finished)
        self.test_worker.progress.connect(self.log_box.append)
        self.test_worker.test_success.connect(self.on_test_success) # 성공 시그널 연결
        self.test_worker.test_error.connect(self.on_test_error) # 오류 시그널 연결
        self.test_worker.finished.connect(self.test_thread.quit)
        self.test_worker.finished.connect(self.test_worker.deleteLater)
        self.test_thread.finished.connect(self.test_thread.deleteLater)
        
        # 스레드 시작
        self.test_thread.start()
    
    def on_test_finished(self):
        """테스트 워커 완료 처리"""
        self.proxy_test_button.setEnabled(True)
        
        # UI 활성화/비활성화 처리
        self.email_edit.setEnabled(False)
        self.password_edit.setEnabled(False)
        self.proxy_server_edit.setEnabled(False)
        self.proxy_username_edit.setEnabled(False)
        self.proxy_password_edit.setEnabled(False)
        self.proxy_test_button.setEnabled(False)
        
        self.search_edit.setEnabled(True)
        self.delay_edit.setEnabled(True)
        self.manual_comments_edit.setEnabled(True)
        self.follow_count_edit.setEnabled(True)
        self.like_range_edit.setEnabled(True)
        self.repost_range_edit.setEnabled(True)
        self.comment_range_edit.setEnabled(True)
        self.interval_combo.setEnabled(True)
        self.run_button.setEnabled(True)
        
        self.log_box.append('✅ 이제 자동화를 시작할 수 있습니다!')

    def on_test_success(self, test_type, proxy_server, ip_info, email, login_status):
        """테스트 성공 시 처리"""
        self.proxy_test_button.setEnabled(True)
        
        # UI 활성화/비활성화 처리
        self.email_edit.setEnabled(False)
        self.password_edit.setEnabled(False)
        self.proxy_server_edit.setEnabled(False)
        self.proxy_username_edit.setEnabled(False)
        self.proxy_password_edit.setEnabled(False)
        self.proxy_test_button.setEnabled(False)
        
        self.search_edit.setEnabled(True)
        self.delay_edit.setEnabled(True)
        self.manual_comments_edit.setEnabled(True)
        self.follow_count_edit.setEnabled(True)
        self.like_range_edit.setEnabled(True)
        self.repost_range_edit.setEnabled(True)
        self.comment_range_edit.setEnabled(True)
        self.interval_combo.setEnabled(True)
        self.run_button.setEnabled(True)
        
        self.log_box.append(f'✅ 이제 자동화를 시작할 수 있습니다!')
        self.log_box.append(f'🌐 연결 방식: {test_type} 연결')
        self.log_box.append(f'📡 프록시 정보: {proxy_server}')
        self.log_box.append(f'📍 현재 IP: {ip_info}')
        self.log_box.append(f'🔐 로그인 계정: {email}')
        self.log_box.append(f'✅ 상태: {login_status}')
        self.log_box.append('💾 세션 저장 완료')

    def on_test_error(self, error_msg):
        """테스트 실패 시 처리"""
        self.proxy_test_button.setEnabled(True)
        
        # UI 활성화/비활성화 처리
        self.email_edit.setEnabled(False)
        self.password_edit.setEnabled(False)
        self.proxy_server_edit.setEnabled(False)
        self.proxy_username_edit.setEnabled(False)
        self.proxy_password_edit.setEnabled(False)
        self.proxy_test_button.setEnabled(False)
        
        self.search_edit.setEnabled(True)
        self.delay_edit.setEnabled(True)
        self.manual_comments_edit.setEnabled(True)
        self.follow_count_edit.setEnabled(True)
        self.like_range_edit.setEnabled(True)
        self.repost_range_edit.setEnabled(True)
        self.comment_range_edit.setEnabled(True)
        self.interval_combo.setEnabled(True)
        self.run_button.setEnabled(True)
        
        self.log_box.append(f'❌ 테스트 실패: {error_msg}')

    def create_test_user_context(self, playwright, email, password, proxy_server, proxy_username, proxy_password):
        """테스트용 브라우저 컨텍스트 생성"""
        session_dir = get_session_dir(email, password)
        
        context_args = {
            'user_data_dir': session_dir,
            'headless': False,
            'args': ['--profile-directory=Default'],
            'ignore_https_errors': True
        }
        
        if proxy_server.strip():
            proxy_config = {'server': proxy_server.strip()}
            
            if proxy_username.strip():
                proxy_config['username'] = proxy_username.strip()
            
            if proxy_password.strip():
                proxy_config['password'] = proxy_password.strip()
            
            context_args['proxy'] = proxy_config
        
        return playwright.chromium.launch_persistent_context(**context_args)
    
    def start_automation(self):
        """자동화 시작"""
        search_query = self.search_edit.text().strip()
        delay_seconds = int(self.delay_edit.text().strip() or "1")
        manual_comments = self.manual_comments_edit.toPlainText().strip()
        email = self.email_edit.text().strip()
        password = self.password_edit.text().strip()
        follow_count = int(self.follow_count_edit.text().strip() or "10")
        like_range = self.like_range_edit.text().strip() or "1~5"
        repost_range = self.repost_range_edit.text().strip() or "0~2"
        comment_range = self.comment_range_edit.text().strip() or "1~3"
        proxy_server = self.proxy_server_edit.text().strip()
        proxy_username = self.proxy_username_edit.text().strip()
        proxy_password = self.proxy_password_edit.text().strip()
        
        if not manual_comments:
            self.log_box.append("⚠️ 랜덤 댓글이 없습니다. 댓글 단계는 건너뛰고 실행합니다.")
        
        if not email or not password:
            self.log_box.append("⚠️ 이메일과 비밀번호를 입력해주세요!")
            return
        
        # 작업 개수 로그 출력
        self.log_box.append(f"📊 작업 개수 설정:")
        self.log_box.append(f"   팔로우: {follow_count}명")
        self.log_box.append(f"   좋아요: {like_range}개 (각 팔로워당)")
        self.log_box.append(f"   리포스트: {repost_range}개 (각 팔로워당)")
        self.log_box.append(f"   댓글: {comment_range}개 (각 팔로워당)")
        
        if proxy_server:
            self.log_box.append(f"🌐 프록시 사용: {proxy_server}")
            if proxy_username:
                self.log_box.append("🔐 프록시 인증 정보 포함")
        else:
            self.log_box.append("🌐 직접 연결 (프록시 미사용)")
        
        self.run_button.setEnabled(False)
        
        # 워커 스레드 생성
        self.thread = QtCore.QThread()
        self.worker = Worker(search_query, delay_seconds, manual_comments, email, password, follow_count, like_range, repost_range, comment_range, proxy_server, proxy_username, proxy_password)
        self.worker.moveToThread(self.thread)
        
        # 시그널 연결
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_finished)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        
        # 스레드 시작
        self.thread.start()
        
        # 타이머 설정
        interval_hours = int(self.interval_combo.currentText())
        interval_ms = interval_hours * 60 * 60 * 1000
        self.timer.start(interval_ms)
        
        self.log_box.append(f"다음 실행 예정: {interval_hours}시간 후")
    
    def on_progress(self, message):
        """진행 상황 업데이트"""
        self.log_box.append(message)
    
    def on_finished(self):
        """작업 완료 처리"""
        self.run_button.setEnabled(True)
        self.log_box.append("작업 완료!")

    def load_saved_config(self):
        """프로그램 시작 시 저장된 설정 자동 로드"""
        config = load_config()
        if config:
            self.email_edit.setText(config.get("email", ""))
            self.password_edit.setText(config.get("password", ""))
            self.proxy_server_edit.setText(config.get("proxy_server", ""))
            self.proxy_username_edit.setText(config.get("proxy_username", ""))
            self.proxy_password_edit.setText(config.get("proxy_password", ""))
            self.search_edit.setText(config.get("search_query", ""))
            self.delay_edit.setText(str(config.get("delay_seconds", "1")))
            self.manual_comments_edit.setText(config.get("manual_comments", ""))
            self.follow_count_edit.setText(str(config.get("follow_count", "10")))
            self.like_range_edit.setText(config.get("like_range", "1~5"))
            self.repost_range_edit.setText(config.get("repost_range", "0~2"))
            self.comment_range_edit.setText(config.get("comment_range", "1~3"))
            self.interval_combo.setCurrentText(str(config.get("interval_hours", "1")))
            self.log_box.append("💾 저장된 설정이 자동으로 로드되었습니다.")

    def save_current_config(self):
        """현재 설정을 파일에 저장"""
        config = {
            "email": self.email_edit.text(),
            "password": self.password_edit.text(),
            "proxy_server": self.proxy_server_edit.text(),
            "proxy_username": self.proxy_username_edit.text(),
            "proxy_password": self.proxy_password_edit.text(),
            "search_query": self.search_edit.text(),
            "delay_seconds": int(self.delay_edit.text() or "1"),
            "manual_comments": self.manual_comments_edit.toPlainText(),
            "follow_count": int(self.follow_count_edit.text() or "10"),
            "like_range": self.like_range_edit.text() or "1~5",
            "repost_range": self.repost_range_edit.text() or "0~2",
            "comment_range": self.comment_range_edit.text() or "1~3",
            "interval_hours": int(self.interval_combo.currentText())
        }
        save_config(config)
        self.log_box.append("설정이 저장되었습니다.")

    def load_current_config(self):
        """저장된 설정을 불러와 UI에 표시"""
        config = load_config()
        if config:
            self.email_edit.setText(config.get("email", ""))
            self.password_edit.setText(config.get("password", ""))
            self.proxy_server_edit.setText(config.get("proxy_server", ""))
            self.proxy_username_edit.setText(config.get("proxy_username", ""))
            self.proxy_password_edit.setText(config.get("proxy_password", ""))
            self.search_edit.setText(config.get("search_query", ""))
            self.delay_edit.setText(str(config.get("delay_seconds", "1")))
            self.manual_comments_edit.setText(config.get("manual_comments", ""))
            self.follow_count_edit.setText(str(config.get("follow_count", "10")))
            self.like_range_edit.setText(config.get("like_range", "1~5"))
            self.repost_range_edit.setText(config.get("repost_range", "0~2"))
            self.comment_range_edit.setText(config.get("comment_range", "1~3"))
            self.interval_combo.setCurrentText(str(config.get("interval_hours", "1")))
            self.log_box.append("설정이 불러와졌습니다.")
        else:
            self.log_box.append("저장된 설정이 없습니다.")

    def clear_log(self):
        """로그 박스의 내용을 지우기"""
        self.log_box.clear()
        self.log_box.append("로그가 지워졌습니다.")

    def save_log(self):
        """로그 박스의 내용을 파일에 저장"""
        file_name, _ = QFileDialog.getSaveFileName(self, "로그 파일 저장", "", "Text Files (*.txt);;All Files (*)")
        if file_name:
            try:
                with open(file_name, 'w', encoding='utf-8') as f:
                    f.write(self.log_box.toPlainText())
                self.log_box.append(f"로그가 '{file_name}'에 저장되었습니다.")
            except Exception as e:
                self.log_box.append(f"로그 저장 실패: {e}")

    def auto_save_config(self):
        """테스트 성공 시 설정을 자동으로 저장"""
        config = {
            "email": self.email_edit.text(),
            "password": self.password_edit.text(),
            "proxy_server": self.proxy_server_edit.text(),
            "proxy_username": self.proxy_username_edit.text(),
            "proxy_password": self.proxy_password_edit.text(),
            "search_query": self.search_edit.text(),
            "delay_seconds": int(self.delay_edit.text() or "1"),
            "manual_comments": self.manual_comments_edit.toPlainText(),
            "follow_count": int(self.follow_count_edit.text() or "10"),
            "like_range": self.like_range_edit.text() or "1~5",
            "repost_range": self.repost_range_edit.text() or "0~2",
            "comment_range": self.comment_range_edit.text() or "1~3",
            "interval_hours": int(self.interval_combo.currentText())
        }
        save_config(config)
        self.log_box.append("테스트 성공 시 설정이 자동으로 저장되었습니다.")

    def auto_save_on_change(self):
        """입력 필드 값이 변경될 때마다 설정을 저장"""
        # 로그 메시지 없이 조용히 저장
        config = {
            "email": self.email_edit.text(),
            "password": self.password_edit.text(),
            "proxy_server": self.proxy_server_edit.text(),
            "proxy_username": self.proxy_username_edit.text(),
            "proxy_password": self.proxy_password_edit.text(),
            "search_query": self.search_edit.text(),
            "delay_seconds": int(self.delay_edit.text() or "1"),
            "manual_comments": self.manual_comments_edit.toPlainText(),
            "follow_count": int(self.follow_count_edit.text() or "10"),
            "like_range": self.like_range_edit.text() or "1~5",
            "repost_range": self.repost_range_edit.text() or "0~2",
            "comment_range": self.comment_range_edit.text() or "1~3",
            "interval_hours": int(self.interval_combo.currentText())
        }
        save_config(config)

class TestWorker(QObject):
    """테스트용 백그라운드 작업 스레드"""
    finished = pyqtSignal()
    progress = pyqtSignal(str)
    test_success = pyqtSignal(str, str, str, str, str)  # 성공 시그널 추가
    test_error = pyqtSignal(str)  # 오류 시그널 추가
    
    def __init__(self, email: str, password: str, proxy_server: str = '', proxy_username: str = '', proxy_password: str = ''):
        super().__init__()
        self.email = email
        self.password = password
        self.proxy_server = proxy_server
        self.proxy_username = proxy_username
        self.proxy_password = proxy_password
    
    def run(self):
        """메인 작업 실행"""
        self.progress.emit('Starting Playwright automation...')
        
        try:
            with sync_playwright() as playwright:
                context = self.create_test_user_context(playwright, self.email, self.password, self.proxy_server, self.proxy_username, self.proxy_password)
                
                if context.pages:
                    page = context.pages[0]
                else:
                    page = context.new_page()
                
                if self.proxy_server:
                    self.progress.emit('🌐 프록시를 통한 IP 확인 중...')
                    page.goto('https://httpbin.org/ip', timeout=15000)
                    ip_info = page.locator('pre').inner_text()
                    self.progress.emit('✅ 프록시 연결 성공!')
                    self.progress.emit(f'📍 프록시 IP 정보: {ip_info.strip()}')
                else:
                    self.progress.emit('🌐 직접 연결로 테스트 중...')
                    ip_info = None
                
                # Threads 메인 페이지로 이동
                self.progress.emit('🌐 Threads 메인 페이지로 이동 중...')
                page.goto('https://www.threads.com', timeout=15000)
                
                # 로그인 상태 확인 (유동적 확인)
                self.progress.emit('🔐 로그인 상태 확인 중...')
                try:
                    if is_login_required(page):
                        # 로그인 페이지로 이동하여 로그인 수행
                        self.progress.emit('🔐 로그인 페이지로 이동 중...')
                        page.goto('https://www.threads.com/login?hl=ko', timeout=15000)
                        
                        self.progress.emit('로그인이 필요합니다. 자동 로그인을 시도합니다...')
                        perform_login(page, self.email, self.password)
                        self.progress.emit(f'✅ 로그인 성공: {self.email}')
                        self.progress.emit('💾 세션이 session_store에 저장되었습니다')
                        login_status = '로그인 완료'
                    else:
                        self.progress.emit('✅ 이미 로그인되어 있습니다')
                        login_status = '기존 세션 사용'
                except Exception as e:
                    self.progress.emit(f'❌ 로그인 상태 확인 실패: {str(e)}')
                    # 안전하게 로그인 시도
                    self.progress.emit('🔐 로그인 페이지로 이동 중...')
                    page.goto('https://www.threads.com/login?hl=ko', timeout=15000)
                    
                    self.progress.emit('로그인이 필요합니다. 자동 로그인을 시도합니다...')
                    perform_login(page, self.email, self.password)
                    self.progress.emit(f'✅ 로그인 성공: {self.email}')
                    self.progress.emit('💾 세션이 session_store에 저장되었습니다')
                    login_status = '로그인 완료'
                
                context.close()
                
                # 성공 시그널 발생 (메인 스레드에서 처리)
                test_type = '프록시' if self.proxy_server else '직접'
                self.test_success.emit(test_type, self.proxy_server, ip_info.strip() if ip_info else 'N/A', self.email, login_status)
                
                # 테스트 성공 시 설정 자동 저장
                self.auto_save_config()
                
        except Exception as e:
            self.progress.emit(f'❌ 테스트 실패: {str(e)}')
            # 오류 시그널 발생 (메인 스레드에서 처리)
            test_type = '프록시' if self.proxy_server else '직접'
            error_msg = f'{test_type} 연결 또는 로그인에 실패했습니다:\n{str(e)}'
            self.test_error.emit(error_msg)
    
    def create_test_user_context(self, playwright, email, password, proxy_server, proxy_username, proxy_password):
        """테스트용 브라우저 컨텍스트 생성"""
        session_dir = get_session_dir(email, password)
        
        context_args = {
            'user_data_dir': session_dir,
            'headless': False,
            'args': ['--profile-directory=Default'],
            'ignore_https_errors': True
        }
        
        if proxy_server.strip():
            proxy_config = {'server': proxy_server.strip()}
            
            if proxy_username.strip():
                proxy_config['username'] = proxy_username.strip()
            
            if proxy_password.strip():
                proxy_config['password'] = proxy_password.strip()
            
            context_args['proxy'] = proxy_config
        
        return playwright.chromium.launch_persistent_context(**context_args)
    
    def auto_save_config(self):
        """테스트 성공 시 설정을 자동으로 저장"""
        config = {
            "email": self.email,
            "password": self.password,
            "proxy_server": self.proxy_server,
            "proxy_username": self.proxy_username,
            "proxy_password": self.proxy_password,
            "search_query": "", # 테스트 컨텍스트에서는 사용하지 않음
            "delay_seconds": 1, # 테스트 컨텍스트에서는 사용하지 않음
            "manual_comments": "", # 테스트 컨텍스트에서는 사용하지 않음
            "follow_count": 10, # 테스트 컨텍스트에서는 사용하지 않음
            "like_range": "1~5", # 테스트 컨텍스트에서는 사용하지 않음
            "repost_range": "0~2", # 테스트 컨텍스트에서는 사용하지 않음
            "comment_range": "1~3", # 테스트 컨텍스트에서는 사용하지 않음
            "interval_hours": 1 # 테스트 컨텍스트에서는 사용하지 않음
        }
        save_config(config)
        self.progress.emit("테스트 성공 시 설정이 자동으로 저장되었습니다.")

def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
