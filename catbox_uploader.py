import requests
import sys
import os

CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"

def upload_file(file_path):
    """
    Catbox.moe에 파일(이미지/동영상 등)을 업로드하고 URL을 반환합니다.
    """
    print(f"      [Catbox] 파일 존재 확인: {file_path}")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"파일이 존재하지 않습니다: {file_path}")
    
    print(f"      [Catbox] 파일 크기 확인...")
    file_size = os.path.getsize(file_path)
    print(f"      [Catbox] 파일 크기: {file_size} bytes")
    
    print(f"      [Catbox] Catbox.moe API 호출 중...")
    print(f"      [Catbox] 업로드 URL: {CATBOX_UPLOAD_URL}")
    
    with open(file_path, 'rb') as f:
        files = {'fileToUpload': f}
        data = {'reqtype': 'fileupload'}
        response = requests.post(CATBOX_UPLOAD_URL, data=data, files=files, timeout=60)
        
        print(f"      [Catbox] 응답 상태 코드: {response.status_code}")
        print(f"      [Catbox] 응답 내용: {response.text[:200]}...")
        
        response.raise_for_status()
        url = response.text.strip()
        
        if url.startswith('http'):
            print(f"      [Catbox] 업로드 성공! URL: {url}")
            return url
        else:
            print(f"      [Catbox] 업로드 실패! 응답: {url}")
            raise Exception(f"Catbox 업로드 실패: {url}")

def upload_multiple(files):
    """
    여러 파일을 순차적으로 업로드하고, URL 리스트를 반환합니다.
    """
    urls = []
    for file_path in files:
        print(f"업로드 중: {file_path}")
        try:
            url = upload_file(file_path)
            print(f"  → 업로드 성공: {url}")
            urls.append(url)
        except Exception as e:
            print(f"  → 업로드 실패: {e}")
    return urls

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python catbox_uploader.py 파일1 [파일2 ...]")
        sys.exit(1)
    file_list = sys.argv[1:]
    result_urls = upload_multiple(file_list)
    print("\n=== 업로드된 파일 URL 목록 ===")
    for url in result_urls:
        print(url) 