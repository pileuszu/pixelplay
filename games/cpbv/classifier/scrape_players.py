# KBO and CPBV Player Database Scraper
# Gathers active rosters, historical players, and pseudonymized mappings to generate dictionary.py

import urllib.request
import urllib.parse
import re
import os
import time
import html.parser

# KBO URL targets
REGISTER_URL = "https://www.koreabaseball.com/Player/Register.aspx"
SEARCH_URL = "https://www.koreabaseball.com/Player/Search.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded"
}

# 196 common KBO player name starting syllables (Korean surnames & foreign syllables)
SURNAMES = [
    "가", "강", "경", "계", "고", "공", "곽", "구", "국", "권", "그", "금", "기", "길", "김",
    "나", "남", "남궁", "네", "노", "니", "다", "단", "대", "더", "데", "도", "동", "디",
    "라", "러", "레", "로", "루", "류", "리", "마", "맹", "메", "명", "모", "목", "몽", "무", "문", "미", "민",
    "바", "박", "반", "발", "방", "배", "백", "밴", "버", "범", "베", "벤", "변", "보", "봉", "뷰", "브", "비", "빈", "빌",
    "사", "살", "상", "새", "샘", "서", "선", "선우", "설", "성", "세", "소", "손", "송", "수", "슈", "스", "시", "신", "심", "싱",
    "아", "안", "알", "애", "앤", "야", "얀", "양", "어", "엄", "에", "엔", "엘", "연", "염", "엽", "오", "옥", "온", "올", "와", "왕", "요", "용", "우", "운", "원", "웰", "위", "윌", "유", "육", "윤", "은", "음", "이", "인", "임",
    "자", "장", "잭", "저", "전", "정", "제", "제갈", "조", "종", "좌", "주", "지", "진",
    "차", "채", "천", "초", "최", "추",
    "카", "캐", "커", "케", "켈", "코", "콜", "쿠", "크", "클", "킹",
    "타", "탁", "테", "토", "트", "티",
    "판", "패", "팽", "페", "편", "평", "포", "폰", "표", "푸", "프", "플", "피", "필",
    "하", "한", "함", "해", "허", "헤", "헥", "현", "형", "호", "홀", "홍", "화", "환", "황", "황보", "후", "희", "히"
]

# KBO Active Team Codes
TEAMS = ["LG", "KT", "SS", "HT", "HH", "OB", "NC", "SK", "LT", "WO"]

# Paths
LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
SCRATCH_DIR = "C:/Users/pilla/.gemini/antigravity-ide/brain/967027df-d85a-4d87-a12f-d06d7fca1652/scratch"
NOTICE_PATH = os.path.join(SCRATCH_DIR, "notice_1.html")
DICTIONARY_PATH = os.path.join(LOCAL_DIR, "dictionary.py")

def get_hidden_fields(html_text):
    fields = {}
    inputs = re.findall(r'<input[^>]*>', html_text, re.IGNORECASE)
    for inp in inputs:
        if re.search(r'type=["\']hidden["\']', inp, re.IGNORECASE):
            name_m = re.search(r'name=["\']([^"\']*)["\']', inp, re.IGNORECASE)
            id_m = re.search(r'id=["\']([^"\']*)["\']', inp, re.IGNORECASE)
            val_m = re.search(r'value=["\']([^"\']*)["\']', inp, re.IGNORECASE)
            name = (name_m.group(1) if name_m else None) or (id_m.group(1) if id_m else None)
            val = val_m.group(1) if val_m else ""
            if name:
                fields[name] = val
    return fields

def scrape_kbo_active():
    """Scrape currently active players (10 teams) from Register.aspx"""
    print("[*] Scraping KBO Active rosters...")
    active_names = set()
    
    try:
        # Initial GET to retrieve ASP.NET state
        req_get = urllib.request.Request(REGISTER_URL, headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req_get) as resp:
            html_get = resp.read().decode('utf-8')
            
        fields = get_hidden_fields(html_get)
        team_key = next((k for k in fields if 'hfSearchTeam' in k), None)
        date_key = next((k for k in fields if 'hfSearchDate' in k), None)
        
        if not team_key:
            print("[-] Error: could not find team search key on Register.aspx")
            return active_names
            
        for team in TEAMS:
            print(f"  Fetching active players for team: {team}")
            data = {
                "__EVENTTARGET": "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$btnCalendarSelect",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": fields.get("__VIEWSTATE", ""),
                "__VIEWSTATEGENERATOR": fields.get("__VIEWSTATEGENERATOR", ""),
                "__EVENTVALIDATION": fields.get("__EVENTVALIDATION", ""),
                team_key: team,
            }
            if date_key:
                data[date_key] = fields.get(date_key, "")
                
            encoded_data = urllib.parse.urlencode(data).encode('utf-8')
            req_post = urllib.request.Request(REGISTER_URL, data=encoded_data, headers=HEADERS)
            
            with urllib.request.urlopen(req_post) as resp_post:
                html_post = resp_post.read().decode('utf-8')
                
            # Parse player names
            # format: playerId=XXXXX">Name</a>
            player_links = re.findall(r'playerId=([0-9]+)[^>]*>([^<]+)</a>', html_post, re.IGNORECASE)
            for _, name in player_links:
                clean_name = name.strip()
                if clean_name:
                    active_names.add(clean_name)
                    
            # Update fields state from post response
            fields = get_hidden_fields(html_post)
            time.sleep(0.05)
            
    except Exception as e:
        print(f"[-] Error scraping active players: {e}")
        
    print(f"[+] Scraped {len(active_names)} active players.")
    return active_names

def scrape_kbo_history():
    """Scrape KBO historical players from Search.aspx by querying surnames"""
    print("[*] Scraping KBO Historical database...")
    history_names = set()
    
    try:
        # Initial GET
        req_get = urllib.request.Request(SEARCH_URL, headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req_get) as resp:
            html_get = resp.read().decode('utf-8')
        fields = get_hidden_fields(html_get)
        
        team_key = 'ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ddlTeam'
        pos_key = 'ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ddlPosition'
        txt_key = 'ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$txtSearchPlayerName'
        btn_key = 'ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$btnSearch'
        
        for idx, surname in enumerate(SURNAMES, 1):
            print(f"  [{idx}/{len(SURNAMES)}] Searching for surname: {surname}...", end="", flush=True)
            
            # Initial search POST for this surname
            data = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": fields.get("__VIEWSTATE", ""),
                "__VIEWSTATEGENERATOR": fields.get("__VIEWSTATEGENERATOR", ""),
                "__EVENTVALIDATION": fields.get("__EVENTVALIDATION", ""),
                team_key: "",
                pos_key: "",
                txt_key: surname,
                btn_key: "검색"
            }
            encoded_data = urllib.parse.urlencode(data).encode('utf-8')
            req_post = urllib.request.Request(SEARCH_URL, data=encoded_data, headers=HEADERS)
            
            with urllib.request.urlopen(req_post) as resp_post:
                html_post = resp_post.read().decode('utf-8')
                
            res_count = re.search(r'검색결과\s*:\s*<span class="point">([0-9]+)</span>건', html_post)
            total = int(res_count.group(1)) if res_count else 0
            print(f" found {total} players.")
            
            if total == 0:
                fields = get_hidden_fields(html_post)
                continue
                
            # Parse page 1
            player_links = re.findall(r'playerId=([0-9]+)[^>]*>([^<]+)</a>', html_post, re.IGNORECASE)
            for _, name in player_links:
                history_names.add(name.strip())
                
            # Determine pages (20 players per page)
            total_pages = (total + 19) // 20
            
            # Paginate through remaining pages
            current_fields = get_hidden_fields(html_post)
            for page in range(2, total_pages + 1):
                # Calculate EVENTTARGET for pagination
                # If page % 5 == 1, click btnNext, else click btnNoX
                if page % 5 == 1:
                    target = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ucPager$btnNext"
                else:
                    no_idx = (page - 1) % 5 + 1
                    target = f"ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$ucPager$btnNo{no_idx}"
                    
                pdata = {
                    "__EVENTTARGET": target,
                    "__EVENTARGUMENT": "",
                    "__VIEWSTATE": current_fields.get("__VIEWSTATE", ""),
                    "__VIEWSTATEGENERATOR": current_fields.get("__VIEWSTATEGENERATOR", ""),
                    "__EVENTVALIDATION": current_fields.get("__EVENTVALIDATION", ""),
                    team_key: "",
                    pos_key: "",
                    txt_key: surname,
                    "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$hfPage": str(page - 1)
                }
                
                encoded_pdata = urllib.parse.urlencode(pdata).encode('utf-8')
                req_page = urllib.request.Request(SEARCH_URL, data=encoded_pdata, headers=HEADERS)
                
                with urllib.request.urlopen(req_page) as resp_page:
                    html_page = resp_page.read().decode('utf-8')
                    
                player_links = re.findall(r'playerId=([0-9]+)[^>]*>([^<]+)</a>', html_page, re.IGNORECASE)
                for _, name in player_links:
                    history_names.add(name.strip())
                    
                current_fields = get_hidden_fields(html_page)
                time.sleep(0.05)
                
            # Prepare state fields for next surname
            fields = current_fields
            time.sleep(0.05)
            
    except Exception as e:
        print(f"\n[-] Error scraping historical players: {e}")
        
    print(f"[+] Scraped {len(history_names)} unique historical players.")
    return history_names

def parse_local_fake_names():
    """Parse local forum notice (notice_1.html) and scraped_community_mappings.txt for real-to-fake mappings"""
    print("[*] Parsing local forum notice and scraped mappings for fake name mappings...")
    fake_names = set()
    
    # 1. Notice HTML
    if os.path.exists(NOTICE_PATH):
        try:
            with open(NOTICE_PATH, "r", encoding="utf-8") as f:
                html_content = f.read()
                
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) >= 2:
                    cleaned = [re.sub(r'<[^>]*>', '', c).strip() for c in cells]
                    if any(h in cleaned for h in ['실명', '가명', '선수명', '변경']):
                        continue
                    for name in cleaned[1:3]:
                        name_clean = re.sub(r'[A-Za-z]+$', '', name).strip()
                        if name_clean:
                            fake_names.add(name_clean)
        except Exception as e:
            print(f"[-] Error parsing notice mappings: {e}")
            
    # 2. Scraped mappings file
    scraped_path = os.path.join(SCRATCH_DIR, "scraped_community_mappings.txt")
    if os.path.exists(scraped_path):
        try:
            with open(scraped_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) == 2:
                        for p in parts:
                            p_clean = re.sub(r'[A-Za-z]+$', '', p).strip()
                            if p_clean:
                                fake_names.add(p_clean)
        except Exception as e:
            print(f"[-] Error parsing scraped mappings file: {e}")
            
    print(f"[+] Scraped {len(fake_names)} names from notice and scraped mappings.")
    return fake_names

def main():
    start_time = time.time()
    
    # 1. Fetch active players
    active = scrape_kbo_active()
    
    # 2. Fetch historical players
    history = scrape_kbo_history()
    
    # 3. Parse local fake names notice mappings
    fakes = parse_local_fake_names()
    
    # 4. Merge all names
    all_names = active.union(history).union(fakes)
    
    # 5. Clean, filter, and normalize names
    cleaned_names = set()
    hangul_pattern = re.compile(r'^[\uAC00-\uD7A3]+$') # only pure Korean names
    
    for name in all_names:
        # Strip suffixes like A, B, C or '93 (if KBO pages contained any)
        name_clean = re.sub(r"'?\d{2}$", "", name)
        name_clean = re.sub(r"[A-Z]$", "", name_clean)
        name_clean = name_clean.strip()
        
        # Verify it consists only of Korean letters and length is between 2 and 5
        if hangul_pattern.match(name_clean) and 2 <= len(name_clean) <= 5:
            cleaned_names.add(name_clean)
            
    # Add a few manual legacy names if they were somehow missed
    legacy_pitchers = [
        "올러", "헤이수스", "노리스", "테런스", "쿠에바스", "벤자민", "켈리", "플럿코", "엔스",
        "앤더슨", "하트", "카스타노", "네일", "크로우", "알드레드", "라우어", "뷰캐넌", "레예스", "코너",
        "반즈", "윌커슨", "페냐", "산체스", "바리아", "와이스", "후라도", "헤이즈", "선동열", "최동원",
        "루친스키", "고우석", "헥터", "소사", "김진우", "유동훈", "한기주", "이대진", "어센시오", "폰세", "요키시",
        "해커", "니퍼트", "밴헤켄", "레일리", "로저스", "스트레일리"
    ]
    for lp in legacy_pitchers:
        cleaned_names.add(lp)
        
    sorted_names = sorted(list(cleaned_names))
    print(f"[+] Total merged database unique names: {len(sorted_names)}")
    
    # 6. Generate dictionary.py content
    # Format KBO_PITCHERS array cleanly
    dict_content = [
        "# KBO / CPBV Player Name Dictionary for OCR Spelling Correction",
        "",
        "KBO_PITCHERS = ["
    ]
    
    # Write names in chunks of 15 names per line for readability
    chunk_size = 15
    for i in range(0, len(sorted_names), chunk_size):
        chunk = sorted_names[i:i + chunk_size]
        line = "    " + ", ".join(f'"{n}"' for n in chunk)
        if i + chunk_size < len(sorted_names):
            line += ","
        dict_content.append(line)
        
    dict_content.append("]")
    dict_content.append("")
    
    # Save to dictionary.py
    with open(DICTIONARY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(dict_content))
    print(f"[+] Successfully generated and updated dictionary.py at {DICTIONARY_PATH}!")
    print(f"[+] Execution completed in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
