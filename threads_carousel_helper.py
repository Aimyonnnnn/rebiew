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

def post_carousel(api_id, access_token, media_items, text, proxies=None):
    """
    여러 이미지와 동영상(캐러셀)을 함께 게시합니다.
    media_items: [{'type': 'IMAGE', 'url': '...'}, {'type': 'VIDEO', 'url': '...'}] 형태의 딕셔너리 리스트
    """
    if not media_items or len(media_items) < 2:
        raise ValueError("캐러셀에는 최소 2개 이상의 미디어가 필요합니다.")
    if len(media_items) > 20:
        raise ValueError("캐러셀은 최대 20개의 미디어만 포함할 수 있습니다.")

    children_ids = []
    has_video = False

    for item in media_items:
        media_type = item.get("type", "").upper()
        media_url = item.get("url")
        if not media_type or not media_url:
            raise ValueError("미디어 아이템은 'type'과 'url'을 포함해야 합니다.")

        item_container_args = {"is_carousel_item": True, "proxies": proxies}
        if media_type == "IMAGE":
            item_container_args["image_url"] = media_url
        elif media_type == "VIDEO":
            item_container_args["video_url"] = media_url
            has_video = True
        else:
            raise ValueError(f"지원하지 않는 미디어 타입입니다: {media_type}. 'IMAGE' 또는 'VIDEO'만 가능합니다.")

        item_container = _create_media_container(api_id, access_token, media_type, **item_container_args)
        container_id = item_container["id"]
        children_ids.append(container_id)

    # 동영상이 하나라도 있으면 충분히 대기 후 시도 + 실패 시 재시도
    max_retries = 5
    initial_wait = 60 if has_video else 5
    retry_wait = 30
    attempt = 0
    last_exception = None

    if has_video:
        time.sleep(initial_wait)

    while attempt < max_retries:
        try:
            carousel_container = _create_carousel_container(api_id, access_token, children_ids, text, proxies=proxies)
            return _publish_container(api_id, carousel_container["id"], access_token, proxies=proxies)
        except Exception as e:
            err_msg = str(e)
            # 하위 요소 오류(400), 미디어 없음(4279009), Invalid parameter 모두 재시도
            if ("하위 요소" in err_msg or 'error_subcode":4279004' in err_msg or 'error_subcode":4279009' in err_msg or "Invalid parameter" in err_msg):
                if attempt < max_retries - 1:
                    print(f"[슬라이드] 동영상/미디어 처리 지연으로 재시도 {attempt+1}/{max_retries}회: {err_msg}")
                    time.sleep(retry_wait)
                    attempt += 1
                    last_exception = e
                    continue
            raise e
    # 모든 재시도 실패 시 마지막 예외 발생
    raise last_exception 