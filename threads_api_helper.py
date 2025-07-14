import requests
import time

API_BASE_URL = "https://graph.threads.net/v1.0"

def _create_media_container(api_id, access_token, media_type, text=None, image_url=None, video_url=None, is_carousel_item=False, proxies=None):
    """미디어 컨테이너(단일, 캐러셀 아이템, 비디오)를 생성합니다."""
    url = f"{API_BASE_URL}/{api_id}/threads"
    data = {
        "media_type": media_type,
        "access_token": access_token,
    }
    if text:
        data["text"] = text
    if image_url:
        data["image_url"] = image_url
    if video_url:
        data["video_url"] = video_url
    if is_carousel_item:
        data["is_carousel_item"] = "true"

    try:
        response = requests.post(url, data=data, proxies=proxies, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP 오류: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"요청 오류: {e}")

def _create_carousel_container(api_id, access_token, children_ids, text, proxies=None):
    """캐러셀 컨테이너를 생성합니다."""
    url = f"{API_BASE_URL}/{api_id}/threads"
    data = {
        "media_type": "CAROUSEL",
        "children": ",".join(children_ids),
        "text": text,
        "access_token": access_token
    }
    try:
        response = requests.post(url, data=data, proxies=proxies, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP 오류: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"요청 오류: {e}")


def _get_container_status(container_id, access_token, proxies=None):
    """미디어 컨테이너의 처리 상태를 확인합니다."""
    url = f"{API_BASE_URL}/{container_id}"
    params = {
        "fields": "status_code",
        "access_token": access_token
    }
    try:
        response = requests.get(url, params=params, proxies=proxies, timeout=10)
        response.raise_for_status()
        return response.json().get("status_code")
    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP 오류: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"요청 오류: {e}")

def _publish_container(api_id, creation_id, access_token, proxies=None):
    """생성된 컨테이너를 최종적으로 게시합니다."""
    url = f"{API_BASE_URL}/{api_id}/threads_publish"
    data = {
        "creation_id": creation_id,
        "access_token": access_token
    }
    try:
        response = requests.post(url, data=data, proxies=proxies, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP 오류: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"요청 오류: {e}")


# --- Public Functions ---

def check_proxy_ip(proxies=None):
    """지정된 프록시를 통해 현재 공인 IP를 확인합니다."""
    url = "https://httpbin.org/ip"
    try:
        response = requests.get(url, proxies=proxies, timeout=15)
        response.raise_for_status()
        origin_ip = response.json().get("origin", "")
        if ',' in origin_ip:
            return origin_ip.split(',')[0].strip()
        return origin_ip
    except requests.exceptions.RequestException as e:
        return False, f"IP 확인 중 오류 발생: {e}"

def post_text(api_id, access_token, text, proxies=None):
    """텍스트만 게시합니다."""
    try:
        container = _create_media_container(api_id, access_token, "TEXT", text=text, proxies=proxies)
        result = _publish_container(api_id, container["id"], access_token, proxies=proxies)
        return True, result
    except Exception as e:
        return False, f"텍스트 게시 실패: {e}"

def post_single_image(api_id, access_token, image_url, text, proxies=None):
    """단일 이미지를 게시합니다."""
    try:
        container = _create_media_container(api_id, access_token, "IMAGE", text=text, image_url=image_url, proxies=proxies)
        result = _publish_container(api_id, container["id"], access_token, proxies=proxies)
        return True, result
    except Exception as e:
        return False, f"단일 이미지 게시 실패: {e}"

def post_carousel(api_id, access_token, image_urls, text, proxies=None):
    """여러 이미지(캐러셀)를 게시합니다."""
    if not image_urls or len(image_urls) < 2:
        return False, "캐러셀에는 최소 2개 이상의 이미지가 필요합니다."
    try:
        children_ids = []
        for url in image_urls:
            item_container = _create_media_container(api_id, access_token, "IMAGE", image_url=url, is_carousel_item=True, proxies=proxies)
            children_ids.append(item_container["id"])
        # 참고코드: 사진만 있을 때는 대기 없이 바로 진행, 동영상만 30초 대기(이 부분은 사용자의 최근 요청대로 유지)
        carousel_container = _create_carousel_container(api_id, access_token, children_ids, text, proxies=proxies)
        result = _publish_container(api_id, carousel_container["id"], access_token, proxies=proxies)
        return True, result
    except Exception as e:
        return False, f"캐러셀 게시 실패: {e}"

def post_video(api_id, access_token, video_url, text, proxies=None):
    """동영상을 게시합니다. (컨테이너 생성 후 20초 대기, 실패 시 10초 후 1회 재시도)"""
    try:
        video_container = _create_media_container(api_id, access_token, "VIDEO", text=text, video_url=video_url, proxies=proxies)
        container_id = video_container["id"]
        time.sleep(20)
        try:
            result = _publish_container(api_id, container_id, access_token, proxies=proxies)
            return True, result
        except Exception as e:
            time.sleep(10)
            result = _publish_container(api_id, container_id, access_token, proxies=proxies)
            return True, result
    except Exception as e:
        return False, f"동영상 게시 실패: {e}"