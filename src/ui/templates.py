import os
import base64

MBTI_CHARACTERS = {
    "ISTJ": "ISTJ.png",
    "ISFJ": "ISFJ.png",
    "INFJ": "INFJ.png",
    "INTJ": "INTJ.png",
    "ISTP": "ISTP.png",
    "ISFP": "ISFP.png",
    "INFP": "INFP.png",
    "INTP": "INTP.png",
    "ESTP": "ESTP.png",
    "ESFP": "ESFP.png",
    "ENFP": "ENFP.png",
    "ENTP": "ENTP.png",
    "ESTJ": "ESTJ.png",
    "ESFJ": "ESFJ.png",
    "ENFJ": "ENFJ.png",
    "ENTJ": "ENTJ.png",
}

def get_image_base64(img_file: str, root_dir: str = None) -> str:
    if root_dir is None:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    img_path = os.path.join(root_dir, "assets", "img", img_file)
    try:
        with open(img_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return ""

SIDEBAR_HIDE_CSS = (
    "<style>"
    "[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl']"
    "{display:none !important}"
    "</style>"
)

def get_sidebar_logo(root_dir: str = None) -> str:
    if root_dir is None:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    img_base64 = get_image_base64("PickCardU.png", root_dir)
    img_data_url = f"data:image/png;base64,{img_base64}"
    
    return (
        '<div class="logo-area">'
        f'  <img src="{img_data_url}" style="width: 240px; height: auto; border-radius: 12px; margin-bottom: 0.8rem;">'
        '  <div class="logo-version">v. 1.0.0</div>'
        '</div>'
    )

SIDEBAR_LOGO = get_sidebar_logo()

def get_splash(root_dir: str = None) -> str:
    if root_dir is None:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    img_base64 = get_image_base64("PickCardU.png", root_dir)
    img_data_url = f"data:image/png;base64,{img_base64}"
    
    return (
        '<div class="splash-overlay">'
        f'  <img src="{img_data_url}" style="width: 600px; height: auto; margin-bottom: 1rem; border-radius: 15px;">'
        '  <p class="splash-tagline">당신을 위한 개인 맞춤형 신용카드 큐레이션 시스템</p>'
        '</div>'
    )

SPLASH = get_splash()

BOT_LOADING = (
    '<div class="msg-bot-row">'
    '<div class="avatar">🤖</div>'
    '<div class="msg-bot-bubble">⏳ 카드 혜택을 검색하고 있습니다...</div>'
    '</div>'
)


def profile_card(user_name: str, mbti_label: str, root_dir: str = None) -> str:
    """MBTI 캐릭터 이미지가 포함된 프로필 카드"""
    if root_dir is None:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    mbti_code = mbti_label.split(" – ")[0].strip() if " – " in mbti_label else mbti_label
    character_file = MBTI_CHARACTERS.get(mbti_code, "PickCardU.png")
    img_base64 = get_image_base64(character_file, root_dir)
    img_data_url = f"data:image/png;base64,{img_base64}"
    
    return (
        '<p class="mypage-section-header">PROFILE</p>'
        '<div class="mypage-profile-card">'
        f'  <div class="mypage-avatar"><img src="{img_data_url}" style="width: 100%; height: 100%; border-radius: 50%; object-fit: cover;"></div>'
        '  <div class="mypage-profile-info">'
        f'    <p class="mypage-info-line"><b>NAME</b> : {user_name}</p>'
        f'    <p class="mypage-info-line"><b>MBTI</b> : {mbti_label}</p>'
        '  </div>'
        '</div>'
    )

def card_tile(card_name: str) -> str:
    return (
        f'<div class="mypage-card-tile">'
        f'  <p class="tile-name">{card_name}</p>'
        f'</div>'
    )

def user_bubble(content: str, mbti_type: str = None, root_dir: str = None) -> str:
    if mbti_type and mbti_type in MBTI_CHARACTERS:
        if root_dir is None:
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        img_b64 = get_image_base64(MBTI_CHARACTERS[mbti_type], root_dir)
        avatar = f'<div class="avatar"><img src="data:image/png;base64,{img_b64}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;"></div>'
    else:
        avatar = '<div class="avatar">👤</div>'
    return (
        '<div class="msg-user-row">'
        f'<div class="msg-user-bubble">{content}</div>'
        f'{avatar}'
        '</div>'
    )

def bot_bubble(content: str) -> str:
    return (
        '<div class="msg-bot-row">'
        '<div class="avatar">🤖</div>'
        f'<div class="msg-bot-bubble">{content}</div>'
        '</div>'
    )
