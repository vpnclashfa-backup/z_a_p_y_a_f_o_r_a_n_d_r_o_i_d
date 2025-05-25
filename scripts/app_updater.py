import requests
from bs4 import BeautifulSoup
import re
import json
import os
from packaging.version import parse, InvalidVersion
from urllib.parse import urljoin, urlparse, unquote
import logging
import time
import sys

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

URL_FILE = "urls_to_check.txt"
TRACKING_FILE = "versions_tracker.json"
OUTPUT_JSON_FILE = "updates_found.json"
GITHUB_OUTPUT_FILE = os.getenv('GITHUB_OUTPUT', 'local_github_output.txt')

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

VERSION_REGEX_PATTERNS = [
    r'(?<![\w.-])(?:[vV])?(\d+(?:\.\d+){1,3}(?:(?:[-._]?[a-zA-Z0-9]+)+)?)(?![.\w])',
    r'(?<![\w.-])(?:[vV])?(\d+(?:\.\d+){1,2})(?![.\w])',
]
VERSION_PATTERNS_FOR_CLEANING = [
    r'\s*[vV]?\d+(?:\.\d+){1,3}(?:(?:[-._]?[a-zA-Z0-9]+)+)?\b',
    r'\s*[vV]?\d+(?:\.\d+){1,2}\b',
    r'\s+\d+(?:\.\d+)*\b' 
]

COMMON_VARIANT_KEYWORDS_TO_CLEAN = [
    "Mod-Extra", "مود اکسترا", "موداکسترا",
    "Mod-Lite", "مود لایت", "مودلایت",
    "Ad-Free", "بدون تبلیغات",
    "Unlocked", "آنلاک شده", "آنلاک",
    "Patched", "پچ شده",
    "Premium", "پرمیوم",
    "Persian", "فارسی",
    "English", "انگلیسی",
    "Universal", "یونیورسال",
    "Original", "اورجینال", "اصلی", "معمولی",
    "Arm64-v8a", "Armeabi-v7a", "x86_64",
    "Arm64", "Armv7", "Arm", "x86", 
    "Windows", "ویندوز", "PC", "کامپیوتر",
    "macOS", "Mac", "OSX", 
    "Linux", "لینوکس", 
    "Ultra", "اولترا",
    "Clone", "کلون",
    "Beta", "بتا",
    "Full", "کامل",
    "Lite", "لایت",
    "Main", # "اصلی" تکراری است، اما Main ممکن است استفاده شود
    "Data", "دیتا", "Obb",
    "Mod", "مود", 
    "Pro", "پرو", 
    "VIP", "وی آی پی",
    "Plus", "پلاس",
    # کلمات کلیدی عمومی برای پاکسازی از نام برنامه پایه
    "Image", "تصویر", 
    "Audio", "صوتی", 
    "Video", "ویدیو", 
    "Document", "سند", "Text", "متن",
    "Archive", "آرشیو", 
    "Font", "فونت"
]
COMMON_VARIANT_REGEX_FOR_CLEANING = r'\b(?:' + '|'.join(re.escape(kw) for kw in COMMON_VARIANT_KEYWORDS_TO_CLEAN) + r')\b'


def load_tracker():
    if os.path.exists(TRACKING_FILE):
        try:
            with open(TRACKING_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logging.info(f"فایل ردیابی {TRACKING_FILE} با موفقیت بارگذاری شد.")
                return data
        except json.JSONDecodeError:
            logging.warning(f"{TRACKING_FILE} خراب است. با ردیاب خالی شروع می شود.")
            return {}
    logging.info(f"فایل ردیابی {TRACKING_FILE} یافت نشد. با ردیاب خالی شروع می شود.")
    return {}

def compare_versions(current_v_str, last_v_str):
    logging.info(f"مقایسه نسخه ها: فعلی='{current_v_str}', قبلی='{last_v_str}'")
    try:
        if not current_v_str:
            logging.warning("نسخه فعلی نامعتبر است (خالی).")
            return False
        if not last_v_str or last_v_str == "0.0.0":
            logging.info(f"نسخه قبلی یافت نشد یا 0.0.0 بود. نسخه فعلی '{current_v_str}' جدید است.")
            return True
        try:
            parsed_current = parse(current_v_str)
            parsed_last = parse(last_v_str)
            if parsed_current > parsed_last:
                return True
            elif parsed_current < parsed_last:
                return False
            else: 
                if current_v_str != last_v_str:
                    return current_v_str > last_v_str
                return False
        except InvalidVersion:
            logging.warning(f"InvalidVersion هنگام تجزیه '{current_v_str}' یا '{last_v_str}'. مقایسه رشته ای.")
            return current_v_str != last_v_str and current_v_str > last_v_str
        except TypeError: 
            logging.warning(f"TypeError هنگام مقایسه '{current_v_str}' با '{last_v_str}'. مقایسه رشته ای.")
            return current_v_str != last_v_str and current_v_str > last_v_str
    except Exception as e:
        logging.error(f"خطا در compare_versions ('{current_v_str}' vs '{last_v_str}'): {e}")
        return current_v_str != last_v_str and current_v_str > last_v_str


def sanitize_text(text, for_filename=False):
    if not text: return ""
    text = text.strip()
    text = re.sub(r'\s*\((?:www\.)?farsroid\.com.*?\)\s*$', '', text, flags=re.IGNORECASE).strip() 
    if for_filename:
        text = text.lower()
        text = text.replace('–', '-').replace('—', '-') 
        text = re.sub(r'[<>:"/\\|?*()\[\]]', '_', text) 
        text = re.sub(r'\s+', '_', text) 
        text = text.replace('-_', '_').replace('_-', '_')
        text = re.sub(r'_+', '_', text) 
        text = text.strip('_') 
    else: 
        text = text.lower()
        text = text.replace('–', '-').replace('—', '-')
        text = re.sub(r'[\(\)\[\]]', '', text) 
        text = re.sub(r'\s+', '_', text)
        text = text.strip('_')
    return text

def extract_app_name_from_page(soup, page_url):
    app_name_candidate = None
    h1_tag = soup.find('h1', class_=re.compile(r'title', re.IGNORECASE))
    if h1_tag and h1_tag.text.strip():
        app_name_candidate = h1_tag.text.strip()
    
    if not app_name_candidate:
        title_tag = soup.find('title')
        if title_tag and title_tag.text.strip():
            app_name_candidate = title_tag.text.strip()
            app_name_candidate = re.sub(r'\s*[-|–—]\s*(?:فارسروید|دانلود.*)$', '', app_name_candidate, flags=re.IGNORECASE).strip()
            app_name_candidate = re.sub(r'\s*–\s*اپلیکیشن.*$', '', app_name_candidate, flags=re.IGNORECASE).strip()

    if app_name_candidate:
        original_name = app_name_candidate 
        if app_name_candidate.lower().startswith("دانلود "):
            app_name_candidate = app_name_candidate[len("دانلود "):].strip()

        for pattern in VERSION_PATTERNS_FOR_CLEANING:
            app_name_candidate = re.sub(pattern, '', app_name_candidate, flags=re.IGNORECASE).strip("-_ ")
        
        app_name_candidate = re.sub(COMMON_VARIANT_REGEX_FOR_CLEANING, '', app_name_candidate, flags=re.IGNORECASE).strip("-_ ")
        
        app_name_candidate = re.sub(r'\s*\((?:www\.)?farsroid\.com.*?\)\s*$', '', app_name_candidate, flags=re.IGNORECASE).strip()
        app_name_candidate = re.sub(r'\s*[-–—]\s*Farsroid\s*$', '', app_name_candidate, flags=re.IGNORECASE).strip()
        
        app_name_candidate = app_name_candidate.strip(' -–—') 
        app_name_candidate = re.sub(r'\s+', ' ', app_name_candidate).strip()

        if app_name_candidate:
            logging.info(f"نام برنامه از H1/Title (اصلی: '{original_name}', پاکسازی شده نهایی: '{app_name_candidate}')")
            return app_name_candidate
    
    logging.info(f"نام برنامه از H1/Title استخراج نشد، تلاش برای استخراج از URL: {page_url}")
    parsed_url = urlparse(page_url)
    path_parts = [part for part in unquote(parsed_url.path).split('/') if part]
    if path_parts:
        guessed_name = path_parts[-1]
        original_guessed_name = guessed_name
        
        known_extensions_regex = r'\.(apk|zip|exe|rar|xapk|apks|msi|dmg|pkg|deb|rpm|appimage|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz|7z|gz|bz2|xz|jpg|jpeg|png|gif|bmp|tiff|tif|webp|svg|ico|mp3|wav|ogg|aac|flac|m4a|wma|mp4|mkv|avi|mov|wmv|flv|webm|mpeg|mpg|txt|pdf|doc|docx|xls|xlsx|ppt|pptx|odt|ods|odp|rtf|csv|html|htm|xml|json|md|ttf|otf|woff|woff2|eot)$'
        guessed_name = re.sub(known_extensions_regex, '', guessed_name, flags=re.IGNORECASE)
        
        for pattern in VERSION_PATTERNS_FOR_CLEANING:
             guessed_name = re.sub(pattern, '', guessed_name, flags=re.IGNORECASE).strip("-_ ")
        
        guessed_name = re.sub(COMMON_VARIANT_REGEX_FOR_CLEANING, '', guessed_name, flags=re.IGNORECASE).strip("-_ ")
        
        generic_keywords_url = r'\b(دانلود|Download|برنامه|App|Apk|Farsroid|Android)\b'
        guessed_name = re.sub(generic_keywords_url, '', guessed_name, flags=re.IGNORECASE).strip("-_ ")
        
        guessed_name = ' '.join(word.capitalize() for word in re.split(r'[-_]+', guessed_name) if word)
        guessed_name = re.sub(r'\s+', ' ', guessed_name).strip()
        
        if guessed_name:
            logging.info(f"نام حدس زده شده از URL (اصلی: '{original_guessed_name}', پاکسازی شده: '{guessed_name}')")
            return guessed_name
            
    logging.warning(f"نام برنامه از هیچ منبعی استخراج نشد. URL: {page_url}")
    return "UnknownApp"

def get_page_source_with_selenium(url, wait_time=20, wait_for_class="downloadbox"):
    # ... (بدون تغییر) ...
    logging.info(f"در حال دریافت {url} با Selenium...")
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu") 
    chrome_options.add_argument("--window-size=1920,1080") 
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")
    driver = None
    try:
        try:
            driver_path = ChromeDriverManager().install()
            service = ChromeService(executable_path=driver_path)
            logging.info(f"ChromeDriverManager در مسیر '{driver_path}' پیدا/نصب شد.")
        except Exception as e_driver_manager:
            logging.warning(f"خطا در استفاده از ChromeDriverManager: {e_driver_manager}. تلاش برای استفاده از درایور پیشفرض سیستم.")
            service = ChromeService()

        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)
        logging.info(f"منتظر بارگذاری محتوای دینامیک (تا {wait_time} ثانیه) برای کلاس '{wait_for_class}'...")
        try:
            WebDriverWait(driver, wait_time).until(
                EC.presence_of_element_located((By.CLASS_NAME, wait_for_class))
            )
            time.sleep(5) 
            logging.info(f"عنصر با کلاس '{wait_for_class}' پیدا شد و زمان اضافی برای بارگذاری داده شد.")
        except Exception as e_wait:
            logging.warning(f"Timeout یا خطا هنگام انتظار برای '{wait_for_class}' در {url}: {e_wait}.")
            if driver: return driver.page_source
            return None
        page_source = driver.page_source
        logging.info(f"موفقیت در دریافت سورس صفحه با Selenium برای {url}")
        return page_source
    except Exception as e:
        logging.error(f"خطای Selenium هنگام دریافت {url}: {e}", exc_info=True)
        return None
    finally:
        if driver:
            driver.quit()
            logging.info("Selenium WebDriver بسته شد.")


def extract_version_from_text_or_url(text_content, url_content):
    # ... (بدون تغییر) ...
    if text_content:
        for pattern in VERSION_REGEX_PATTERNS:
            match = re.search(pattern, text_content)
            if match:
                return match.group(1).strip("-_ ")
            
    if url_content:
        for pattern in VERSION_REGEX_PATTERNS:
            match = re.search(pattern, url_content) 
            if match:
                return match.group(1).strip("-_ ")
            
    fallback_pattern = r'(\d+\.\d+(?:\.\d+){0,2}(?:[.-]?[a-zA-Z0-9]+)*)' 
    if text_content:
        match = re.search(fallback_pattern, text_content)
        if match: return match.group(1).strip("-_ ")
    if url_content:
        match = re.search(fallback_pattern, url_content)
        if match: return match.group(1).strip("-_ ")
    return None


def get_file_extension_from_url(download_url, combined_text_for_variant):
    parsed_url_path = urlparse(download_url).path
    raw_filename_from_url = os.path.basename(parsed_url_path)
    
    # اولویت با پسوندهای دو قسمتی
    double_extensions = [".tar.gz", ".tar.bz2", ".tar.xz"]
    for de in double_extensions:
        if raw_filename_from_url.lower().endswith(de): return de

    _, ext_from_url = os.path.splitext(raw_filename_from_url)
    
    known_extensions = [
        '.apk', '.zip', '.exe', '.rar', '.xapk', '.apks', '.7z', '.gz', '.bz2', '.xz',
        '.msi', '.dmg', '.pkg', '.deb', '.rpm', '.appimage',
        '.tgz', '.tbz2', '.txz', 
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.svg', '.ico',
        '.mp3', '.wav', '.ogg', '.aac', '.flac', '.m4a', '.wma',
        '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mpeg', '.mpg',
        '.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
        '.odt', '.ods', '.odp', '.rtf', '.csv', '.html', '.htm', '.xml', '.json', '.md',
        '.ttf', '.otf', '.woff', '.woff2', '.eot'
    ]
    
    if ext_from_url and ext_from_url.lower() in known_extensions:
        return ext_from_url.lower()
    else:
        # تعیین پیش‌فرض بر اساس نوع محتوا اگر پسوند ناشناخته بود
        if "windows" in combined_text_for_variant or "pc" in combined_text_for_variant : return ".exe" 
        if "macos" in combined_text_for_variant or "mac" in combined_text_for_variant: return ".dmg"
        if "linux" in combined_text_for_variant : return ".appimage" 
        if "data" in combined_text_for_variant or "obb" in combined_text_for_variant : return ".zip" 
        if "font" in combined_text_for_variant: return ".zip" # فونت ها اغلب در zip هستند
        # اگر هیچ کدام، و پسوندی وجود داشت، همان را برگردان
        if ext_from_url: return ext_from_url.lower()
        return ".bin" # یک پسوند عمومی برای فایل‌های باینری ناشناخته


def scrape_farsroid_page(page_url, soup, tracker_data):
    updates_found_on_page = []
    page_app_name_full = extract_app_name_from_page(soup, page_url) 
    logging.info(f"پردازش صفحه: {page_url} (نام پایه برنامه: '{page_app_name_full}')")

    download_box = soup.find('section', class_='downloadbox')
    if not download_box:
        logging.warning(f"باکس دانلود در {page_url} پیدا نشد.")
        return updates_found_on_page
    
    download_links_ul = download_box.find('ul', class_='download-links')
    if not download_links_ul:
        logging.warning(f"لیست لینک های دانلود در {page_url} پیدا نشد.")
        return updates_found_on_page
        
    found_lis = download_links_ul.find_all('li', class_='download-link')
    if not found_lis:
        logging.warning("هیچ آیتم li.download-link پیدا نشد.")
        return updates_found_on_page

    logging.info(f"تعداد {len(found_lis)} آیتم li.download-link پیدا شد.")

    for i, li in enumerate(found_lis):
        logging.info(f"--- پردازش li شماره {i+1} ---")
        link_tag = li.find('a', class_='download-btn')
        if not link_tag or not link_tag.get('href'):
            logging.warning(f"  تگ دانلود معتبر در li {i+1} پیدا نشد.")
            continue

        download_url = urljoin(page_url, link_tag['href'])
        link_text_span = link_tag.find('span', class_='txt')
        link_text = link_text_span.text.strip() if link_text_span else ""

        logging.info(f"  URL: {download_url}")
        logging.info(f"  متن لینک: {link_text}")

        filename_from_url_decoded = unquote(urlparse(download_url).path.split('/')[-1])
        logging.info(f"  نام فایل از URL (decoded): {filename_from_url_decoded}")
        
        current_version = extract_version_from_text_or_url(link_text, filename_from_url_decoded)

        if not current_version:
            logging.warning(f"  نسخه از '{link_text}' یا '{filename_from_url_decoded}' استخراج نشد.")
            continue
        logging.info(f"  نسخه استخراج شده: {current_version}")

        variant_parts = []
        combined_text_for_variant = (filename_from_url_decoded.lower() + " " + link_text.lower()).replace('(farsroid.com)', '').replace('دانلود فایل نصبی', '').replace('برنامه با لینک مستقیم', '').strip()
        combined_text_for_variant = re.sub(r'\b(?:با لینک مستقیم|مگابایت|\d+)\b', '', combined_text_for_variant, flags=re.IGNORECASE).strip()

        # --- تشخیص نوع (Variant) ---
        # (کدهای تشخیص نوع مانند قبل، با کلمات کلیدی اضافه شده)
        if 'mod-extra' in combined_text_for_variant or 'مود اکسترا' in combined_text_for_variant: variant_parts.append("Mod-Extra")
        elif 'mod-lite' in combined_text_for_variant or 'مود لایت' in combined_text_for_variant: variant_parts.append("Mod-Lite")
        elif 'mod' in combined_text_for_variant or 'مود شده' in combined_text_for_variant : variant_parts.append("Mod")
        
        if 'premium' in combined_text_for_variant or 'پرمیوم' in combined_text_for_variant:
            if not any(p.lower().startswith("mod") for p in variant_parts): variant_parts.append("Premium")

        if 'ultra' in combined_text_for_variant or 'اولترا' in combined_text_for_variant: variant_parts.append("Ultra")
        if 'unlocked' in combined_text_for_variant or 'آنلاک' in combined_text_for_variant: variant_parts.append("Unlocked")
        if 'ad-free' in combined_text_for_variant or 'بدون تبلیغات' in combined_text_for_variant: variant_parts.append("Ad-Free")
        if 'patched' in combined_text_for_variant or 'پچ شده' in combined_text_for_variant: variant_parts.append("Patched")
        if 'vip' in combined_text_for_variant: variant_parts.append("VIP")
        if 'plus' in combined_text_for_variant or 'پلاس' in combined_text_for_variant: variant_parts.append("Plus")
        if 'clone' in combined_text_for_variant or 'کلون' in combined_text_for_variant: variant_parts.append("Clone")
        if 'full' in combined_text_for_variant or 'کامل' in combined_text_for_variant:
            if not any(k in p.lower() for p in variant_parts for k in ["mod", "premium", "unlocked", "vip", "plus", "pro"]): 
                 variant_parts.append("Full")
        if 'beta' in combined_text_for_variant or 'بتا' in combined_text_for_variant: variant_parts.append("Beta")
        if 'pro' in combined_text_for_variant or 'پرو' in combined_text_for_variant:
            if not any(k in p.lower() for p in variant_parts for k in ["mod", "premium", "unlocked", "vip", "plus", "full"]):
                variant_parts.append("Pro")

        if not any("lite" in p.lower() for p in variant_parts) and ('lite' in combined_text_for_variant or 'لایت' in combined_text_for_variant):
             variant_parts.append("Lite")
 
        if 'persian' in combined_text_for_variant or 'فارسی' in combined_text_for_variant: variant_parts.append("Persian")
        elif 'english' in combined_text_for_variant or 'انگلیسی' in combined_text_for_variant:
            if not any("Persian" in p for p in variant_parts): variant_parts.append("English")
        
        arch_found = False
        if 'arm64-v8a' in combined_text_for_variant or 'arm64' in combined_text_for_variant: variant_parts.append("Arm64-v8a"); arch_found=True
        elif 'armeabi-v7a' in combined_text_for_variant or 'armv7' in combined_text_for_variant: variant_parts.append("Armeabi-v7a"); arch_found=True
        elif 'arm' in combined_text_for_variant and not arch_found: variant_parts.append("Arm"); arch_found=True
        elif 'x86_64' in combined_text_for_variant: variant_parts.append("x86_64"); arch_found=True
        elif 'x86' in combined_text_for_variant and not arch_found : variant_parts.append("x86"); arch_found=True

        file_extension = get_file_extension_from_url(download_url, combined_text_for_variant)
        logging.info(f"  پسوند فایل نهایی: {file_extension}")
        
        # --- ادامه تشخیص نوع بر اساس پسوند ---
        # این بخش برای اضافه کردن نوع سیستم عامل یا دسته بندی کلی است اگر نوع خاصی قبلا تشخیص داده نشده
        if not variant_parts: # فقط اگر هیچ نوع خاصی (mod, premium, etc.) تشخیص داده نشده
            if file_extension in [".exe", ".msi"]: variant_parts.append("Windows")
            elif file_extension in [".dmg", ".pkg"]: variant_parts.append("macOS")
            elif file_extension in [".deb", ".rpm", ".appimage"]: variant_parts.append("Linux")
            elif file_extension in [".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz"]:
                if "data" in combined_text_for_variant or "obb" in combined_text_for_variant: variant_parts.append("Data")
                else: variant_parts.append("Archive")
            elif file_extension in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.svg', '.ico']: variant_parts.append("Image")
            elif file_extension in ['.mp3', '.wav', '.ogg', '.aac', '.flac', '.m4a', '.wma']: variant_parts.append("Audio")
            elif file_extension in ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mpeg', '.mpg']: variant_parts.append("Video")
            elif file_extension in ['.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf', '.csv', '.html', '.htm', '.xml', '.json', '.md']: variant_parts.append("Document")
            elif file_extension in ['.ttf', '.otf', '.woff', '.woff2', '.eot']: variant_parts.append("Font")
        
        if file_extension == ".apk" and not variant_parts and not arch_found:
            if 'universal' in combined_text_for_variant or 'اصلی' in combined_text_for_variant or 'original' in combined_text_for_variant or 'معمولی' in combined_text_for_variant:
                variant_parts.append("Universal")
            elif 'main' in combined_text_for_variant:
                 variant_parts.append("Main")

        unique_variant_parts = sorted(list(set(p for p in variant_parts if p)))
        if not unique_variant_parts:
            if file_extension == ".apk": variant_final = "Universal" 
            else: variant_final = "Default" # یک پیشفرض کلی اگر هیچ چیز دیگری نبود
        else:
            variant_final = "-".join(unique_variant_parts)
            if not variant_final: 
                 variant_final = "Universal" if file_extension == ".apk" else "Default"
        
        logging.info(f"  نوع (Variant) نهایی: {variant_final}")

        base_app_name_for_id = page_app_name_full 
        base_app_name_for_id_cleaned = re.sub(COMMON_VARIANT_REGEX_FOR_CLEANING, '', base_app_name_for_id, flags=re.IGNORECASE).strip("-_ ")
        base_app_name_for_id_cleaned = re.sub(r'\s*[vV]?' + re.escape(current_version) + r'\b', '', base_app_name_for_id_cleaned, flags=re.IGNORECASE).strip("-_ ")
        if not base_app_name_for_id_cleaned: base_app_name_for_id_cleaned = "App" 

        tracking_id_app_part = sanitize_text(base_app_name_for_id_cleaned, for_filename=False)
        tracking_id_variant_part = sanitize_text(variant_final, for_filename=False)
        tracking_id = f"{tracking_id_app_part}_{tracking_id_variant_part}".lower()
        tracking_id = re.sub(r'_+', '_', tracking_id).strip('_')
        logging.info(f"  شناسه ردیابی: {tracking_id}")
        
        app_name_for_file_base = page_app_name_full 
        app_name_for_file_base_cleaned = re.sub(COMMON_VARIANT_REGEX_FOR_CLEANING, '', app_name_for_file_base, flags=re.IGNORECASE).strip("-_ ")
        for pattern in VERSION_PATTERNS_FOR_CLEANING:
            app_name_for_file_base_cleaned = re.sub(pattern, '', app_name_for_file_base_cleaned, flags=re.IGNORECASE).strip("-_ ")
        if not app_name_for_file_base_cleaned: app_name_for_file_base_cleaned = "App"

        app_name_sanitized = sanitize_text(app_name_for_file_base_cleaned, for_filename=True)
        version_for_file = sanitize_text(current_version, for_filename=True).replace('.', '_')
        variant_sanitized_for_file = sanitize_text(variant_final, for_filename=True)

        filename_constructor_parts = [app_name_sanitized]
        if version_for_file:
            filename_constructor_parts.append(f"v{version_for_file}")
        
        # انواع عمومی را به نام فایل اضافه نکن اگر تنها نوع هستند
        generic_types_for_filename_exclusion = ["archive", "image", "audio", "video", "document", "text", "font", "default", "universal", "main", "windows", "linux", "macos"]
        if variant_sanitized_for_file and variant_sanitized_for_file.lower() not in generic_types_for_filename_exclusion:
            filename_constructor_parts.append(variant_sanitized_for_file)
        elif variant_sanitized_for_file and variant_sanitized_for_file.lower() in generic_types_for_filename_exclusion and len(variant_parts) > 1 : # اگر بیش از یک بخش در variant_parts بود (یعنی نوع عمومی + نوع خاص)
             filename_constructor_parts.append(variant_sanitized_for_file) # در این حالت اضافه کن
            
        suggested_filename = "_".join(filter(None, filename_constructor_parts)) + file_extension
        suggested_filename = re.sub(r'_+', '_', suggested_filename).strip('_')
        suggested_filename = re.sub(r'^_+|_+$', '', suggested_filename) 
        suggested_filename = re.sub(r'_+', '_', suggested_filename)

        logging.info(f"  نام فایل پیشنهادی: {suggested_filename}")
        
        last_known_version = tracker_data.get(tracking_id, "0.0.0")
        if compare_versions(current_version, last_known_version):
            logging.info(f"    => آپدیت جدید برای {tracking_id}: {current_version} (قبلی: {last_known_version})")
            updates_found_on_page.append({
                "app_name": page_app_name_full, 
                "version": current_version,
                "variant": variant_final,
                "download_url": download_url,
                "page_url": page_url,
                "tracking_id": tracking_id,
                "suggested_filename": suggested_filename,
                "current_version_for_tracking": current_version
            })
        else:
            logging.info(f"    => {tracking_id} به‌روز است (فعلی: {current_version}, قبلی: {last_known_version}).")
    return updates_found_on_page

def main():
    # ... (بدون تغییر) ...
    if not os.path.exists(URL_FILE):
        logging.error(f"فایل URL ها یافت نشد: {URL_FILE}")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if os.getenv('GITHUB_OUTPUT'):
            with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output: gh_output.write(f"updates_count=0\n")
        sys.exit(1) 

    with open(URL_FILE, 'r', encoding='utf-8') as f:
        urls_to_process = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not urls_to_process:
        logging.info("فایل URL ها خالی است یا فقط شامل کامنت است.")
        with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f: json.dump([], f)
        if os.getenv('GITHUB_OUTPUT'):
            with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output: gh_output.write(f"updates_count=0\n")
        return

    tracker_data = load_tracker()
    all_updates_found = []
    
    for page_url in urls_to_process:
        logging.info(f"\n--- شروع بررسی URL: {page_url} ---")
        page_content = get_page_source_with_selenium(page_url, wait_for_class="downloadbox") 
        
        if not page_content:
            logging.error(f"محتوای صفحه برای {page_url} با Selenium دریافت نشد. رد شدن...")
            continue
        try:
            soup = BeautifulSoup(page_content, 'html.parser')
            if "farsroid.com" in page_url.lower(): 
                updates_on_page = scrape_farsroid_page(page_url, soup, tracker_data)
                all_updates_found.extend(updates_on_page)
            else:
                logging.warning(f"خراش دهنده برای {page_url} پیاده سازی نشده است.")
        except Exception as e:
            logging.error(f"خطا هنگام پردازش محتوای دریافت شده از Selenium برای {page_url}: {e}", exc_info=True)
        logging.info(f"--- پایان بررسی URL: {page_url} ---")

    new_tracker_data_for_save = tracker_data.copy()
    for update_item in all_updates_found:
        new_tracker_data_for_save[update_item["tracking_id"]] = update_item["current_version_for_tracking"]

    with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_updates_found, f, ensure_ascii=False, indent=2)
    
    try:
        with open(TRACKING_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_tracker_data_for_save, f, ensure_ascii=False, indent=2)
        logging.info(f"فایل ردیاب {TRACKING_FILE} با موفقیت بروزرسانی شد.")
    except Exception as e:
        logging.error(f"خطا در ذخیره فایل ردیاب {TRACKING_FILE}: {e}")

    num_updates = len(all_updates_found)
    if os.getenv('GITHUB_OUTPUT'): 
        with open(GITHUB_OUTPUT_FILE, 'a', encoding='utf-8') as gh_output:
            gh_output.write(f"updates_count={num_updates}\n")
    logging.info(f"\nخلاصه: {num_updates} آپدیت پیدا شد. جزئیات در {OUTPUT_JSON_FILE}")


if __name__ == "__main__":
    main()
