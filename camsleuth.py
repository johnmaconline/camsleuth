##########################################################################################
#
# Script name: camsleuth.py
#
# Description: Access, inspect, map, and lightly search open/public trail-camera databases.
#
# Author: John Macdonald
#
##########################################################################################

import argparse
import csv
import html
from html.parser import HTMLParser
import json
import logging
import os
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path
from urllib import error
from urllib import request
from urllib.parse import quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

# ****************************************************************************************
# Global data and configuration
# ****************************************************************************************

SCRIPT_NAME = os.path.basename(sys.argv[0])
DEFAULT_DB_CONFIG = 'open_trailcam_dbs.json'
DEFAULT_CREDS_CONFIG = 'trailcam_creds.local.json'
DEFAULT_PERSONAL_SOURCES_CONFIG = 'personal_trailcam_sources.json'
DEFAULT_SOCIAL_SOURCES_CONFIG = 'social_trailcam_sources.json'
DEFAULT_SOCIAL_MANUAL_SEEDS = 'social_manual_seeds.csv'
DEFAULT_LOG_FILE = 'camsleuth.log'
DEFAULT_CACHE_DIR = './trailcam_cache'
DEFAULT_API_MAP_DIR = './trailcam_api_maps'
DEFAULT_PERSONAL_CACHE_DIR = './trailcam_personal_cache'
DEFAULT_SOCIAL_CACHE_DIR = './trailcam_social_cache'
DEFAULT_TIMEOUT_SEC = 30
MAX_TEXT_BYTES = 1024 * 1024
PERSONAL_CONFIG_PRIORITIES = {'high', 'medium_high', 'medium', 'low'}
PERSONAL_CONFIG_REQUIRED_KEYS = (
    'source_id', 'display_name', 'source_type', 'platform', 'base_url',
    'entry_urls', 'license_status', 'ingestion_status')
SOCIAL_CONFIG_REQUIRED_KEYS = (
    'source_id', 'display_name', 'platform', 'source_type', 'base_url',
    'query_terms', 'collection_mode', 'auth_mode', 'license_status',
    'permission_status')
SOCIAL_CREDENTIAL_KEYS = {
    'youtube_data_api_key': '',
    'instagram_graph_api_token': '',
    'tiktok_api_token': '',
    'meta_api_token': ''
}
SOCIAL_HIGH_VALUE_TERMS = {
    'bobcat', 'coyote', 'bear', 'mountain lion', 'cougar', 'panther',
    'wolf', 'deer', 'turkey', 'fox', 'raccoon', 'vehicle', 'person'
}
SPECIES_TERMS = [
    'deer', 'whitetail', 'white-tailed deer', 'elk', 'moose', 'bear', 'black bear',
    'grizzly', 'coyote', 'fox', 'red fox', 'gray fox', 'bobcat', 'lynx',
    'mountain lion', 'cougar', 'panther', 'wolf', 'raccoon', 'opossum', 'possum',
    'skunk', 'squirrel', 'rabbit', 'hare', 'turkey', 'wild turkey', 'otter',
    'stoat', 'badger', 'pine marten', 'marten', 'alligator', 'quail',
    'roadrunner', 'bird', 'owl', 'hawk', 'eagle', 'beaver', 'groundhog',
    'woodchuck', 'porcupine', 'fisher', 'weasel', 'mink', 'vehicle', 'person'
]
BROAD_LOCATION_TERMS = [
    'alaska', 'arizona', 'california', 'colorado', 'florida', 'idaho', 'montana',
    'new york', 'northern california', 'colorado front range', 'pennsylvania',
    'tanzania', 'new zealand', 'ontario', 'canada', 'united states', 'uk',
    'england', 'scotland', 'wyoming', 'yellowstone', 'front range',
    'state game lands', 'national park', 'pa', 'berks county', 'oley',
    'oley valley', 'reading', 'philadelphia', 'new jersey', 'delaware',
    'maryland', 'ohio', 'west virginia', 'virginia', 'appalachia', 'poconos'
]

# Logging config
log = logging.getLogger(SCRIPT_NAME)
log.setLevel(logging.DEBUG)

# File handler for logging
fh = logging.FileHandler(DEFAULT_LOG_FILE, mode='w')
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    '%(asctime)-15s [%(funcName)25s:%(lineno)-5s] %(levelname)-8s %(message)s')
fh.setFormatter(formatter)
log.addHandler(fh)

log.debug('Global data and configuration for this script...')

# ****************************************************************************************
# Exceptions
# ****************************************************************************************

class Error(Exception):
    '''
    Base class for exceptions in this module.
    '''
    pass

class ConfigError(Error):
    '''
    Raised when a configuration file is missing or invalid.
    '''
    pass

class RequestError(Error):
    '''
    Raised when a URL cannot be fetched.
    '''
    def __init__(self, url, message=None):
        self.url = url
        self.message = message or f'Failed to fetch URL: {url}'
        super().__init__(self.message)

class PersonalConfigError(ConfigError):
    '''
    Raised when the personal sources config is invalid.
    '''
    pass

class SimpleHTMLExtractor(HTMLParser):
    '''
    Lightweight HTML extractor for public-page metadata.
    '''
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.skip_depth = 0
        self.title_parts = []
        self.text_parts = []
        self.links = []
        self.images = []
        self.videos = []
        self.canonical_url = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.skip_depth += 1
            return
        if tag == 'title':
            self.in_title = True
        if tag == 'link' and attrs.get('rel'):
            rel_values = attrs.get('rel')
            if isinstance(rel_values, str) and 'canonical' in rel_values.lower():
                self.canonical_url = attrs.get('href')
        if tag == 'a' and attrs.get('href'):
            self.links.append({'url': attrs.get('href'), 'text': attrs.get('title', '')})
        if tag == 'img' and attrs.get('src'):
            self.images.append({
                'url': attrs.get('src'),
                'alt_text': attrs.get('alt', ''),
                'width': attrs.get('width'),
                'height': attrs.get('height')
            })
        if tag in ('video', 'source', 'iframe'):
            src = attrs.get('src') or attrs.get('data-src')
            if src and any(token in src.lower() for token in ('youtube', 'youtu.be', 'vimeo', '.mp4', '.mov', 'video')):
                self.videos.append({'url': src, 'title': attrs.get('title', '')})

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip_depth:
            self.skip_depth -= 1
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = ' '.join(data.split())
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        self.text_parts.append(text)

# ****************************************************************************************
# Functions
# ****************************************************************************************

def load_json(path):
    '''
    Load JSON from disk.
    '''
    log.debug(f'Loading JSON: {path}')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise ConfigError(f'Missing config file: {path}') from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f'Invalid JSON in {path}: {exc}') from exc

def write_json(path, payload):
    '''
    Write pretty JSON to disk.
    '''
    log.debug(f'Writing JSON: {path}')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write('\n')

def write_jsonl(path, rows):
    '''
    Write JSONL rows to disk.
    '''
    log.debug(f'Writing JSONL: {path}')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True))
            f.write('\n')

def read_jsonl(path):
    '''
    Read JSONL rows from disk.
    '''
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def write_csv(path, fieldnames, rows):
    '''
    Write CSV rows to disk.
    '''
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, '') for key in fieldnames})

def safe_filename(value):
    '''
    Convert text to a safe filename.
    '''
    value = re.sub(r'[^A-Za-z0-9_.-]+', '_', value.strip())
    return value.strip('_') or 'file'

def get_databases(config, db_id=None):
    '''
    Return configured databases, optionally filtered to one id.
    '''
    databases = config.get('databases', [])
    if db_id:
        databases = [db for db in databases if db.get('id') == db_id]
        if not databases:
            raise ConfigError(f'No database found with id: {db_id}')
    return databases

def get_defaults(config):
    '''
    Return config defaults with script fallbacks.
    '''
    defaults = config.get('defaults', {})
    return {
        'cache_dir': defaults.get('cache_dir', DEFAULT_CACHE_DIR),
        'api_map_dir': defaults.get('api_map_dir', DEFAULT_API_MAP_DIR),
        'timeout_sec': int(defaults.get('timeout_sec', DEFAULT_TIMEOUT_SEC)),
        'user_agent': defaults.get('user_agent', f'{SCRIPT_NAME}/0.1')
    }

def get_personal_defaults(config):
    '''
    Return personal-source defaults with script fallbacks.
    '''
    defaults = config.get('default_rules', {})
    return {
        'download_media_by_default': bool(defaults.get('download_media_by_default', False)),
        'respect_robots_txt': bool(defaults.get('respect_robots_txt', True)),
        'max_pages_per_source': int(defaults.get('max_pages_per_source', 50)),
        'request_timeout_seconds': int(defaults.get('request_timeout_seconds', DEFAULT_TIMEOUT_SEC)),
        'user_agent': defaults.get('user_agent', f'{SCRIPT_NAME}/0.1 research metadata crawler'),
        'cache_dir': DEFAULT_PERSONAL_CACHE_DIR
    }

def get_social_defaults(config):
    '''
    Return social-source defaults with script fallbacks.
    '''
    defaults = config.get('default_rules', {})
    return {
        'collection_mode': defaults.get('collection_mode', 'metadata_only'),
        'download_media_by_default': bool(defaults.get('download_media_by_default', False)),
        'respect_robots_txt': bool(defaults.get('respect_robots_txt', True)),
        'allow_logged_in_scraping': bool(defaults.get('allow_logged_in_scraping', False)),
        'allow_private_group_scraping': bool(defaults.get('allow_private_group_scraping', False)),
        'allow_private_account_scraping': bool(defaults.get('allow_private_account_scraping', False)),
        'allow_media_download': bool(defaults.get('allow_media_download', False)),
        'request_timeout_seconds': int(defaults.get('request_timeout_seconds', DEFAULT_TIMEOUT_SEC)),
        'max_results_per_query': int(defaults.get('max_results_per_query', 50)),
        'user_agent': defaults.get('user_agent', f'{SCRIPT_NAME}/0.1 social metadata discovery'),
        'cache_dir': DEFAULT_SOCIAL_CACHE_DIR
    }

def load_personal_sources_config(path):
    '''
    Load the personal/small-collection sources config.
    '''
    return load_json(path)

def validate_personal_sources_config(config):
    '''
    Validate personal source config and return a summary.
    '''
    errors = []
    if 'version' not in config:
        errors.append('Missing top-level key: version')
    if 'sources' not in config:
        errors.append('Missing top-level key: sources')
    sources = config.get('sources', [])
    if not isinstance(sources, list):
        errors.append('Top-level key "sources" must be a list')
        sources = []

    seen = set()
    validated = []
    for source in sources:
        source_id = source.get('source_id')
        if not isinstance(source, dict):
            errors.append(f'Invalid source entry: {source!r}')
            continue
        for key in PERSONAL_CONFIG_REQUIRED_KEYS:
            if key not in source:
                errors.append(f'{source_id or "unknown"} missing required key: {key}')
        if source_id:
            if not re.fullmatch(r'[a-z0-9_]+', source_id):
                errors.append(f'Invalid source_id (must be lowercase snake_case): {source_id}')
            if source_id in seen:
                errors.append(f'Duplicate source_id: {source_id}')
            seen.add(source_id)
        if not isinstance(source.get('entry_urls'), list) or not source.get('entry_urls'):
            errors.append(f'{source_id or "unknown"} entry_urls must be a non-empty list')
        priority = source.get('priority')
        if priority and priority not in PERSONAL_CONFIG_PRIORITIES:
            errors.append(f'{source_id or "unknown"} invalid priority: {priority}')
        validated.append({
            'source_id': source_id,
            'display_name': source.get('display_name'),
            'valid': source_id not in seen or True
        })

    if errors:
        raise PersonalConfigError('; '.join(errors))

    return {
        'valid': True,
        'source_count': len(sources),
        'sources': [{'source_id': item.get('source_id'), 'display_name': item.get('display_name'), 'valid': True} for item in validated]
    }

def get_personal_sources(config, source_id=None):
    '''
    Return configured personal sources, optionally filtered.
    '''
    sources = config.get('sources', [])
    if source_id and source_id != 'all':
        sources = [source for source in sources if source.get('source_id') == source_id]
        if not sources:
            raise PersonalConfigError(f'No personal source found with id: {source_id}')
    return sources

def load_social_sources_config(path):
    '''
    Load the social discovery config.
    '''
    return load_json(path)

def validate_social_sources_config(config):
    '''
    Validate social-source config and return a summary.
    '''
    errors = []
    if 'version' not in config:
        errors.append('Missing top-level key: version')
    if 'sources' not in config:
        errors.append('Missing top-level key: sources')
    sources = config.get('sources', [])
    if not isinstance(sources, list):
        errors.append('Top-level key "sources" must be a list')
        sources = []
    seen = set()
    validated = []
    for source in sources:
        if not isinstance(source, dict):
            errors.append(f'Invalid social source entry: {source!r}')
            continue
        source_id = source.get('source_id')
        for key in SOCIAL_CONFIG_REQUIRED_KEYS:
            if key not in source:
                errors.append(f'{source_id or "unknown"} missing required key: {key}')
        if source_id:
            if not re.fullmatch(r'[a-z0-9_]+', source_id):
                errors.append(f'Invalid social source_id (must be lowercase snake_case): {source_id}')
            if source_id in seen:
                errors.append(f'Duplicate social source_id: {source_id}')
            seen.add(source_id)
        if not isinstance(source.get('query_terms'), list) or not source.get('query_terms'):
            errors.append(f'{source_id or "unknown"} query_terms must be a non-empty list')
        priority = source.get('priority')
        if priority and priority not in PERSONAL_CONFIG_PRIORITIES:
            errors.append(f'{source_id or "unknown"} invalid priority: {priority}')
        validated.append({'source_id': source_id, 'display_name': source.get('display_name'), 'valid': True})
    if errors:
        raise PersonalConfigError('; '.join(errors))
    return {'valid': True, 'source_count': len(sources), 'sources': validated}

def get_social_sources(config, source_id=None):
    '''
    Return configured social sources, optionally filtered.
    '''
    sources = config.get('sources', [])
    if source_id and source_id != 'all':
        sources = [source for source in sources if source.get('source_id') == source_id]
        if not sources:
            raise PersonalConfigError(f'No social source found with id: {source_id}')
    return sources

def get_social_source_cache_dir(defaults, source):
    '''
    Return the cache directory for a social source.
    '''
    return Path(defaults['cache_dir']) / safe_filename(source.get('source_id', 'social'))

def get_personal_source_cache_dir(defaults, source):
    '''
    Return the cache directory for a personal source.
    '''
    return Path(defaults['cache_dir']) / safe_filename(source.get('source_id', 'source'))

def detect_platform_from_url(url):
    '''
    Infer a platform from a URL.
    '''
    host = urlparse(url).netloc.lower()
    if 'blogspot.' in host:
        return 'blogspot'
    if 'wordpress.' in host:
        return 'wordpress'
    if 'flickr.com' in host:
        return 'flickr'
    if 'smugmug.com' in host:
        return 'smugmug'
    if 'youtube.com' in host or 'youtu.be' in host:
        return 'youtube'
    if 'forum' in host or 'forum' in url.lower():
        return 'forum'
    if '.gov' in host:
        return 'government_gallery'
    return 'custom'

def extract_species_terms(*values):
    '''
    Extract species terms from text values.
    '''
    text = ' '.join(str(value or '') for value in values).lower()
    found = []
    for term in SPECIES_TERMS:
        if term.lower() in text:
            found.append(term)
    return sorted(set(found))

def extract_broad_location_terms(*values):
    '''
    Extract broad location terms from text values.
    '''
    text = ' '.join(str(value or '') for value in values).lower()
    found = []
    for term in BROAD_LOCATION_TERMS:
        if term in text:
            found.append(term)
    return sorted(set(found))

def extract_contact_links(links):
    '''
    Extract likely contact/about links.
    '''
    results = []
    for link in links:
        url = (link.get('url') or '').lower()
        if any(token in url for token in ('contact', 'about', 'profile', 'author', 'privacy')):
            results.append(link.get('url'))
    return sorted(set(results))

def detect_license_status(text, source_default='unknown_contact_required'):
    '''
    Infer a conservative license state from page text.
    '''
    lower = (text or '').lower()
    if 'creative commons' in lower or 'cc-by' in lower:
        return 'creative_commons_confirmed'
    if 'public domain' in lower:
        return 'public_domain_confirmed'
    if 'all rights reserved' in lower:
        return 'do_not_download'
    if 'terms of use' in lower or 'copyright' in lower:
        return 'check_site_terms'
    return source_default

def extract_hashtags(*values):
    '''
    Extract hashtags from text values.
    '''
    text = ' '.join(str(value or '') for value in values)
    return sorted(set(tag.lower() for tag in re.findall(r'#([A-Za-z0-9_]+)', text)))

def score_social_lead(record):
    '''
    Compute a simple lead score for a social discovery record.
    '''
    text = ' '.join([
        str(record.get('title', '')),
        str(record.get('caption_text', '')),
        str(record.get('description', '')),
        ' '.join(record.get('hashtags', [])),
        ' '.join(record.get('species_terms', [])),
        ' '.join(record.get('broad_location', [])),
        str(record.get('creator_display_name', ''))
    ]).lower()
    score = 0
    if record.get('creator_post_count_seen', 0) > 1:
        score += 3
    if any(term in text for term in ('trailcam', 'trail camera', 'camera trap', 'game cam')):
        score += 3
    if any(term in record.get('broad_location', []) for term in ('pennsylvania', 'pa', 'berks county', 'oley', 'oley valley')):
        score += 3
    if any(term in SOCIAL_HIGH_VALUE_TERMS for term in record.get('species_terms', [])):
        score += 2
    if record.get('contact_path'):
        score += 2
    if record.get('platform') == 'youtube':
        score += 2
    if record.get('platform') in ('instagram', 'tiktok', 'facebook'):
        score += 1
    return score

def detect_login_required(text, final_url):
    '''
    Infer whether login is required.
    '''
    lower = (text or '').lower()
    url = (final_url or '').lower()
    return any(token in lower for token in ('sign in', 'log in', 'password')) or 'login' in url

def check_robots_allowed(url, user_agent):
    '''
    Check robots.txt for one URL.
    '''
    parsed = urlparse(url)
    robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
        return {
            'robots_url': robots_url,
            'robots_allowed': parser.can_fetch(user_agent, url),
            'robots_status': 'checked'
        }
    except Exception as exc:
        return {
            'robots_url': robots_url,
            'robots_allowed': None,
            'robots_status': f'unknown: {exc}'
        }

def normalize_links(base_url, rows, field='url'):
    '''
    Resolve relative URLs against a base URL.
    '''
    normalized = []
    for row in rows:
        url = row.get(field)
        if not url:
            continue
        item = dict(row)
        item[field] = urljoin(base_url, url)
        normalized.append(item)
    return normalized

def parse_html_page(source, page_url, body, status_code, final_url, robots_info):
    '''
    Extract normalized page metadata from HTML.
    '''
    extractor = SimpleHTMLExtractor()
    extractor.feed(body)
    title = ' '.join(extractor.title_parts).strip()
    visible_text = ' '.join(extractor.text_parts).strip()
    canonical_url = urljoin(page_url, extractor.canonical_url) if extractor.canonical_url else final_url or page_url
    links = normalize_links(final_url or page_url, extractor.links)
    images = normalize_links(final_url or page_url, extractor.images)
    videos = normalize_links(final_url or page_url, extractor.videos)
    matched_seed_terms = [
        term for term in source.get('search_strategy', {}).get('seed_queries', [])
        if term.lower() in visible_text.lower() or term.lower() in title.lower()
    ]
    species_terms = extract_species_terms(title, visible_text, ' '.join(image.get('alt_text', '') for image in images))
    broad_location = extract_broad_location_terms(title, visible_text, page_url)
    license_status = detect_license_status(visible_text, source.get('license_status', 'unknown_contact_required'))
    caption_text = ' '.join(filter(None, [image.get('alt_text', '') for image in images]))[:4000]
    page_date = None
    date_match = re.search(r'\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2}|[A-Z][a-z]+ \d{1,2}, \d{4})\b', visible_text)
    if date_match:
        page_date = date_match.group(1)
    return {
        'source_id': source.get('source_id'),
        'source_url': source.get('base_url'),
        'page_url': final_url or page_url,
        'canonical_url': canonical_url,
        'page_title': title,
        'page_date': page_date,
        'visible_text': visible_text[:20000],
        'matched_seed_terms': matched_seed_terms,
        'links': [link.get('url') for link in links[:500]],
        'image_urls': [image.get('url') for image in images[:500]],
        'video_urls': [video.get('url') for video in videos[:200]],
        'caption_text': caption_text,
        'possible_contact_urls': extract_contact_links(links),
        'license_terms_detected': license_status,
        'license_status': license_status,
        'species_terms': species_terms,
        'broad_location': broad_location,
        'robots_status': robots_info.get('robots_status'),
        'robots_allowed': robots_info.get('robots_allowed'),
        'http_status': status_code,
        'fetched_at': date.today().isoformat()
    }

def build_media_rows(page):
    '''
    Build per-media metadata rows from a page record.
    '''
    rows = []
    for url in page.get('image_urls', []):
        rows.append({
            'source_id': page.get('source_id'),
            'page_url': page.get('page_url'),
            'media_url': url,
            'media_type': 'image',
            'alt_text': '',
            'caption_text': page.get('caption_text', ''),
            'nearby_text': page.get('visible_text', '')[:1000],
            'width': None,
            'height': None,
            'license_status': page.get('license_status'),
            'download_allowed': False
        })
    for url in page.get('video_urls', []):
        rows.append({
            'source_id': page.get('source_id'),
            'page_url': page.get('page_url'),
            'media_url': url,
            'media_type': 'video',
            'alt_text': '',
            'caption_text': page.get('caption_text', ''),
            'nearby_text': page.get('visible_text', '')[:1000],
            'width': None,
            'height': None,
            'license_status': page.get('license_status'),
            'download_allowed': False
        })
    return rows

class PersonalSourceAdapter:
    '''
    Base adapter for personal/public lead sources.
    '''
    def check(self, source, rules):
        return check_personal_source(source, rules)

    def discover(self, source, rules):
        return discover_personal_source(source, rules)

    def search(self, source, rules, terms, limit):
        return search_personal_source(source, rules, terms, limit)

    def export_lead(self, source, rules):
        cache_dir = get_personal_source_cache_dir(rules, source)
        return build_lead_row(source, load_source_summary(cache_dir))

class BlogspotAdapter(PersonalSourceAdapter):
    pass

class WordpressAdapter(PersonalSourceAdapter):
    pass

class FlickrAdapter(PersonalSourceAdapter):
    pass

class SmugMugAdapter(PersonalSourceAdapter):
    pass

class YouTubeAdapter(PersonalSourceAdapter):
    pass

class ForumAdapter(PersonalSourceAdapter):
    pass

class StaticSiteAdapter(PersonalSourceAdapter):
    pass

class GovernmentGalleryAdapter(PersonalSourceAdapter):
    pass

class SocialSourceAdapter:
    '''
    Base adapter for social discovery sources.
    '''
    def validate(self, source):
        return []

    def check(self, source, creds, rules):
        return check_social_source(source, creds, rules)

    def discover(self, source, creds, rules):
        return discover_social_source(source, creds, rules)

    def search_cache(self, source, rules, terms, limit):
        return search_social_source(source, rules, terms, limit)

class YouTubeSocialAdapter(SocialSourceAdapter):
    pass

class InstagramSocialAdapter(SocialSourceAdapter):
    pass

class TikTokSocialAdapter(SocialSourceAdapter):
    pass

class FacebookSocialAdapter(SocialSourceAdapter):
    pass

class ManualReviewSocialAdapter(SocialSourceAdapter):
    pass

def get_personal_adapter(source):
    '''
    Return the adapter instance for a source.
    '''
    platform = source.get('platform')
    if platform == 'blogspot':
        return BlogspotAdapter()
    if platform == 'wordpress':
        return WordpressAdapter()
    if platform == 'flickr':
        return FlickrAdapter()
    if platform == 'smugmug':
        return SmugMugAdapter()
    if platform == 'youtube':
        return YouTubeAdapter()
    if platform == 'forum':
        return ForumAdapter()
    if platform == 'government_gallery':
        return GovernmentGalleryAdapter()
    return StaticSiteAdapter()

def get_social_adapter(source):
    '''
    Return the social adapter instance for a source.
    '''
    platform = source.get('platform')
    if platform == 'youtube':
        return YouTubeSocialAdapter()
    if platform == 'instagram':
        return InstagramSocialAdapter()
    if platform == 'tiktok':
        return TikTokSocialAdapter()
    if platform == 'facebook':
        return FacebookSocialAdapter()
    return ManualReviewSocialAdapter()

def load_source_summary(cache_dir):
    '''
    Load one source summary if present.
    '''
    path = Path(cache_dir) / 'source_summary.json'
    if path.exists():
        return load_json(path)
    return None

def check_personal_source(source, rules):
    '''
    Check entry-URL accessibility for one personal source.
    '''
    results = []
    for url in source.get('entry_urls', []):
        robots_info = {'robots_status': 'not_checked', 'robots_allowed': None}
        if rules.get('respect_robots_txt'):
            robots_info = check_robots_allowed(url, rules.get('user_agent'))
        if robots_info.get('robots_allowed') is False:
            results.append({
                'source_id': source.get('source_id'),
                'display_name': source.get('display_name'),
                'entry_url': url,
                'http_status': None,
                'final_url': url,
                'robots_status': robots_info.get('robots_status'),
                'robots_allowed': False,
                'likely_platform': detect_platform_from_url(url),
                'login_required': False,
                'public_content_visible': False,
                'error': 'blocked_by_robots'
            })
            continue
        try:
            resp = http_fetch(
                url,
                method='GET',
                timeout=rules['request_timeout_seconds'],
                user_agent=rules['user_agent'],
                max_bytes=MAX_TEXT_BYTES)
            body = resp.get('body', b'').decode('utf-8', errors='replace')
            visible = strip_html(body)[:2000]
            results.append({
                'source_id': source.get('source_id'),
                'display_name': source.get('display_name'),
                'entry_url': url,
                'http_status': resp.get('status_code'),
                'final_url': resp.get('final_url') or url,
                'robots_status': robots_info.get('robots_status'),
                'robots_allowed': robots_info.get('robots_allowed'),
                'likely_platform': detect_platform_from_url(resp.get('final_url') or url),
                'login_required': detect_login_required(body, resp.get('final_url')),
                'public_content_visible': bool(visible),
                'error': resp.get('error')
            })
        except RequestError as exc:
            results.append({
                'source_id': source.get('source_id'),
                'display_name': source.get('display_name'),
                'entry_url': url,
                'http_status': None,
                'final_url': url,
                'robots_status': robots_info.get('robots_status'),
                'robots_allowed': robots_info.get('robots_allowed'),
                'likely_platform': detect_platform_from_url(url),
                'login_required': False,
                'public_content_visible': False,
                'error': exc.message
            })
    return {
        'source_id': source.get('source_id'),
        'display_name': source.get('display_name'),
        'results': results
    }

def discover_personal_source(source, rules):
    '''
    Discover metadata for one personal source.
    '''
    cache_dir = get_personal_source_cache_dir(rules, source)
    pages = []
    media_rows = []
    visited = set()
    queue = list(source.get('entry_urls', []))
    max_pages = max(1, min(rules.get('max_pages_per_source', 50), 5))

    while queue and len(visited) < max_pages:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        robots_info = {'robots_status': 'not_checked', 'robots_allowed': None}
        if rules.get('respect_robots_txt'):
            robots_info = check_robots_allowed(page_url, rules.get('user_agent'))
        if robots_info.get('robots_allowed') is False:
            continue
        try:
            resp = http_fetch(
                page_url,
                method='GET',
                timeout=rules['request_timeout_seconds'],
                user_agent=rules['user_agent'],
                max_bytes=MAX_TEXT_BYTES)
        except RequestError as exc:
            log.warning(f'Personal source fetch failed for {page_url}: {exc.message}')
            continue
        content_type = (resp.get('headers') or {}).get('Content-Type', '')
        if 'html' not in content_type and 'xml' not in content_type and 'text' not in content_type:
            continue
        body = resp.get('body', b'').decode('utf-8', errors='replace')
        page = parse_html_page(source, page_url, body, resp.get('status_code'), resp.get('final_url'), robots_info)
        pages.append(page)
        media_rows.extend(build_media_rows(page))
        parsed_base = urlparse(source.get('base_url', ''))
        for link in page.get('links', [])[:100]:
            parsed_link = urlparse(link)
            if not parsed_link.scheme.startswith('http'):
                continue
            if parsed_link.netloc != parsed_base.netloc:
                continue
            if link not in visited and link not in queue:
                queue.append(link)
            if len(queue) + len(visited) >= max_pages:
                break

    summary = {
        'source_id': source.get('source_id'),
        'display_name': source.get('display_name'),
        'source_type': source.get('source_type'),
        'platform': source.get('platform'),
        'base_url': source.get('base_url'),
        'entry_urls': source.get('entry_urls', []),
        'priority': source.get('priority', ''),
        'license_status': source.get('license_status', 'unknown_contact_required'),
        'ingestion_status': source.get('ingestion_status'),
        'contact_path': sorted(set(url for page in pages for url in page.get('possible_contact_urls', []))),
        'species_seen': sorted(set(term for page in pages for term in page.get('species_terms', []))),
        'broad_location': sorted(set(term for page in pages for term in page.get('broad_location', []))),
        'media_type': sorted(set(row.get('media_type') for row in media_rows)),
        'page_count': len(pages),
        'media_count': len([row for row in media_rows if row.get('media_type') == 'image']),
        'video_count': len([row for row in media_rows if row.get('media_type') == 'video']),
        'notes': source.get('notes', '')
    }

    cache_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(cache_dir / 'pages.jsonl', pages)
    write_jsonl(cache_dir / 'media_index.jsonl', media_rows)
    write_json(cache_dir / 'source_summary.json', summary)
    return summary

def search_record_matches(record, terms):
    '''
    Return True when all terms appear in the record text.
    '''
    haystack = json.dumps({
        'page_title': record.get('page_title'),
        'visible_text': record.get('visible_text'),
        'caption_text': record.get('caption_text'),
        'species_terms': record.get('species_terms'),
        'broad_location': record.get('broad_location'),
        'media_urls': record.get('image_urls') or record.get('media_urls'),
        'video_urls': record.get('video_urls'),
        'notes': record.get('notes')
    }, sort_keys=True).lower()
    return all(term.lower() in haystack for term in terms)

def search_personal_source(source, rules, terms, limit):
    '''
    Search cached personal-source metadata.
    '''
    cache_dir = get_personal_source_cache_dir(rules, source)
    pages_path = cache_dir / 'pages.jsonl'
    if not pages_path.exists():
        raise ConfigError(f'No cache for personal source {source.get("source_id")}. Run --discover first.')
    pages = read_jsonl(pages_path)
    results = []
    for page in pages:
        if search_record_matches(page, terms):
            results.append({
                'source_id': source.get('source_id'),
                'display_name': source.get('display_name'),
                'page_url': page.get('page_url'),
                'page_title': page.get('page_title'),
                'page_date': page.get('page_date'),
                'matched_terms': [term for term in terms if term.lower() in json.dumps(page).lower()],
                'species_terms': page.get('species_terms', []),
                'broad_location': page.get('broad_location', []),
                'media_urls': page.get('image_urls', []),
                'video_urls': page.get('video_urls', []),
                'caption_text': page.get('caption_text', ''),
                'license_status': page.get('license_status', source.get('license_status')),
                'notes': source.get('notes', '')
            })
            if len(results) >= limit:
                break
    return results

def build_lead_row(source, summary=None):
    '''
    Build one CSV lead row.
    '''
    summary = summary or {}
    return {
        'source_id': source.get('source_id'),
        'display_name': source.get('display_name'),
        'source_type': source.get('source_type'),
        'platform': source.get('platform'),
        'base_url': source.get('base_url'),
        'entry_urls': '|'.join(source.get('entry_urls', [])),
        'priority': source.get('priority', ''),
        'license_status': source.get('license_status', ''),
        'ingestion_status': source.get('ingestion_status', ''),
        'contact_path': '|'.join(summary.get('contact_path', [])),
        'species_seen': '|'.join(summary.get('species_seen', [])),
        'broad_location': '|'.join(summary.get('broad_location', [])),
        'media_type': '|'.join(summary.get('media_type', [])),
        'page_count': summary.get('page_count', 0),
        'media_count': summary.get('media_count', 0),
        'video_count': summary.get('video_count', 0),
        'notes': source.get('notes', '')
    }

def export_personal_leads(sources, rules, output_path):
    '''
    Export a CSV lead list for personal sources.
    '''
    rows = []
    for source in sources:
        summary = load_source_summary(get_personal_source_cache_dir(rules, source))
        rows.append(build_lead_row(source, summary))
    fieldnames = [
        'source_id', 'display_name', 'source_type', 'platform', 'base_url',
        'entry_urls', 'priority', 'license_status', 'ingestion_status',
        'contact_path', 'species_seen', 'broad_location', 'media_type',
        'page_count', 'media_count', 'video_count', 'notes'
    ]
    write_csv(output_path, fieldnames, rows)
    return {'output': output_path, 'row_count': len(rows)}

def export_personal_results(results, output_path):
    '''
    Export cached search results for personal sources.
    '''
    fieldnames = [
        'source_id', 'display_name', 'page_url', 'page_title', 'page_date',
        'matched_terms', 'species_terms', 'broad_location', 'media_urls',
        'video_urls', 'caption_text', 'license_status', 'notes'
    ]
    rows = []
    for result in results:
        rows.append({
            'source_id': result.get('source_id'),
            'display_name': result.get('display_name'),
            'page_url': result.get('page_url'),
            'page_title': result.get('page_title'),
            'page_date': result.get('page_date'),
            'matched_terms': '|'.join(result.get('matched_terms', [])),
            'species_terms': '|'.join(result.get('species_terms', [])),
            'broad_location': '|'.join(result.get('broad_location', [])),
            'media_urls': '|'.join(result.get('media_urls', [])),
            'video_urls': '|'.join(result.get('video_urls', [])),
            'caption_text': result.get('caption_text', ''),
            'license_status': result.get('license_status', ''),
            'notes': result.get('notes', '')
        })
    write_csv(output_path, fieldnames, rows)
    return {'output': output_path, 'row_count': len(rows)}

def build_social_post_record(source, query, post_url='', creator_handle='', creator_display_name='', profile_url='',
                             title='', caption_text='', description='', hashtags=None, broad_location=None,
                             species_terms=None, media_type='manual_review', posted_at='',
                             engagement_summary=None, contact_path='', notes='', creator_post_count_seen=0):
    '''
    Build one social metadata record.
    '''
    record = {
        'source_id': source.get('source_id'),
        'platform': source.get('platform'),
        'source_type': source.get('source_type'),
        'query': query,
        'post_url': post_url,
        'creator_handle': creator_handle,
        'creator_display_name': creator_display_name,
        'profile_url': profile_url,
        'title': title,
        'caption_text': caption_text,
        'description': description,
        'hashtags': hashtags or [],
        'species_terms': species_terms or [],
        'broad_location': broad_location or [],
        'media_type': media_type,
        'posted_at': posted_at,
        'engagement_summary': engagement_summary or {'views': None, 'likes': None, 'comments': None},
        'contact_path': contact_path,
        'license_status': source.get('license_status', 'unknown_contact_required'),
        'permission_status': source.get('permission_status', 'not_requested'),
        'media_downloaded': False,
        'fetched_at': date.today().isoformat(),
        'notes': notes,
        'creator_post_count_seen': creator_post_count_seen
    }
    record['lead_score'] = score_social_lead(record)
    return record

def build_social_creator_records(source, posts):
    '''
    Aggregate creator records from social post records.
    '''
    grouped = {}
    for post in posts:
        key = post.get('profile_url') or post.get('creator_handle') or post.get('creator_display_name') or post.get('query')
        item = grouped.setdefault(key, {
            'source_id': source.get('source_id'),
            'platform': source.get('platform'),
            'creator_handle': post.get('creator_handle', ''),
            'creator_display_name': post.get('creator_display_name', ''),
            'profile_url': post.get('profile_url', ''),
            'post_count_seen': 0,
            'species_terms_seen': set(),
            'broad_locations_seen': set(),
            'sample_post_urls': [],
            'contact_path': post.get('contact_path', ''),
            'license_status': post.get('license_status', source.get('license_status')),
            'permission_status': post.get('permission_status', source.get('permission_status')),
            'priority': source.get('priority', 'medium'),
            'notes': source.get('notes', '')
        })
        item['post_count_seen'] += 1
        item['species_terms_seen'].update(post.get('species_terms', []))
        item['broad_locations_seen'].update(post.get('broad_location', []))
        if post.get('post_url') and len(item['sample_post_urls']) < 10:
            item['sample_post_urls'].append(post.get('post_url'))
    rows = []
    for item in grouped.values():
        item['species_terms_seen'] = sorted(item['species_terms_seen'])
        item['broad_locations_seen'] = sorted(item['broad_locations_seen'])
        rows.append(item)
    return rows

def check_social_source(source, creds, rules):
    '''
    Check one social source for readiness.
    '''
    credential_key = ((source.get('official_api') or {}).get('credential_key')) or ''
    credential_present = bool(creds.get(credential_key)) if credential_key else False
    http_status = None
    final_url = source.get('base_url')
    if source.get('platform') == 'youtube':
        try:
            resp = http_fetch(source.get('base_url'), 'GET', rules['request_timeout_seconds'], rules['user_agent'], max_bytes=32768)
            http_status = resp.get('status_code')
            final_url = resp.get('final_url') or final_url
        except RequestError:
            http_status = None
    status = 'ready'
    if source.get('platform') in ('instagram', 'tiktok', 'facebook'):
        status = 'manual_review_only'
    elif source.get('platform') == 'youtube' and not credential_present:
        status = 'ready'
    if http_status and http_status >= 400:
        status = 'unavailable'
    return {
        'source_id': source.get('source_id'),
        'platform': source.get('platform'),
        'auth_mode': source.get('auth_mode'),
        'official_api_configured': bool((source.get('official_api') or {}).get('available')),
        'credential_present': credential_present,
        'base_url_http_status': http_status,
        'final_url': final_url,
        'status': status
    }

def discover_youtube_public(source, rules):
    '''
    Discover YouTube leads using public search-result metadata.
    '''
    posts = []
    seen = set()
    for query in source.get('query_terms', [])[:10]:
        search_url = 'https://www.youtube.com/results?search_query=' + quote(query)
        try:
            resp = http_fetch(search_url, 'GET', rules['request_timeout_seconds'], rules['user_agent'], max_bytes=MAX_TEXT_BYTES)
        except RequestError:
            continue
        body = resp.get('body', b'').decode('utf-8', errors='replace')
        video_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', body)
        titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]\}', body)
        channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', body)
        for idx, video_id in enumerate(video_ids[:min(5, rules.get('max_results_per_query', 50))]):
            post_url = f'https://www.youtube.com/watch?v={video_id}'
            if post_url in seen:
                continue
            seen.add(post_url)
            title = html.unescape(titles[idx]) if idx < len(titles) else query
            channel = html.unescape(channels[idx]) if idx < len(channels) else ''
            hashtags = extract_hashtags(title, query)
            description = ''
            species_terms = extract_species_terms(title, query, channel)
            broad_location = extract_broad_location_terms(title, query, channel)
            posts.append(build_social_post_record(
                source,
                query=query,
                post_url=post_url,
                creator_display_name=channel,
                profile_url='https://www.youtube.com/',
                title=title,
                caption_text=title,
                description=description,
                hashtags=hashtags,
                species_terms=species_terms,
                broad_location=broad_location,
                media_type='video',
                posted_at='',
                contact_path='https://www.youtube.com/',
                notes=source.get('notes', '')
            ))
    return posts

def discover_social_source(source, creds, rules):
    '''
    Discover metadata-only social leads for one source.
    '''
    cache_dir = get_social_source_cache_dir(rules, source)
    if source.get('platform') == 'youtube':
        posts = discover_youtube_public(source, rules)
        if not posts:
            posts = [build_social_post_record(
                source,
                query=query,
                title=f'Manual review seed: {query}',
                caption_text=query,
                description='Public web discovery produced no results; manual review suggested.',
                hashtags=extract_hashtags(query),
                species_terms=extract_species_terms(query),
                broad_location=extract_broad_location_terms(query),
                media_type='manual_review',
                notes=source.get('notes', '')
            ) for query in source.get('query_terms', [])]
    else:
        posts = [build_social_post_record(
            source,
            query=query,
            title=f'Manual review seed: {query}',
            caption_text=query,
            description='Restricted platform. Manual review or official API access required.',
            hashtags=extract_hashtags(query),
            species_terms=extract_species_terms(query),
            broad_location=extract_broad_location_terms(query),
            media_type='manual_review',
            notes=source.get('notes', '')
        ) for query in source.get('query_terms', [])]

    creators = build_social_creator_records(source, posts)
    for post in posts:
        matches = [item for item in creators if item.get('profile_url') == post.get('profile_url') and item.get('creator_display_name') == post.get('creator_display_name')]
        post['creator_post_count_seen'] = matches[0].get('post_count_seen', 0) if matches else 0
        post['lead_score'] = score_social_lead(post)

    summary = {
        'source_id': source.get('source_id'),
        'platform': source.get('platform'),
        'source_type': source.get('source_type'),
        'query_count': len(source.get('query_terms', [])),
        'post_count': len(posts),
        'creator_count': len(creators),
        'manual_review_only': source.get('platform') in ('instagram', 'tiktok', 'facebook'),
        'license_status': source.get('license_status'),
        'permission_status': source.get('permission_status'),
        'priority': source.get('priority'),
        'notes': source.get('notes', '')
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(cache_dir / 'posts.jsonl', posts)
    write_jsonl(cache_dir / 'creators.jsonl', creators)
    write_json(cache_dir / 'source_summary.json', summary)
    return summary

def search_social_source(source, rules, terms, limit):
    '''
    Search cached social metadata.
    '''
    cache_dir = get_social_source_cache_dir(rules, source)
    posts_path = cache_dir / 'posts.jsonl'
    if not posts_path.exists():
        raise ConfigError(f'No cache for social source {source.get("source_id")}. Run --discover-social first.')
    posts = read_jsonl(posts_path)
    results = []
    for post in posts:
        haystack = json.dumps({
            'title': post.get('title'),
            'caption_text': post.get('caption_text'),
            'description': post.get('description'),
            'hashtags': post.get('hashtags'),
            'species_terms': post.get('species_terms'),
            'broad_location': post.get('broad_location'),
            'creator_handle': post.get('creator_handle'),
            'creator_display_name': post.get('creator_display_name'),
            'profile_url': post.get('profile_url'),
            'post_url': post.get('post_url'),
            'notes': post.get('notes')
        }, sort_keys=True).lower()
        if all(term.lower() in haystack for term in terms):
            results.append({
                'source_id': post.get('source_id'),
                'platform': post.get('platform'),
                'post_url': post.get('post_url'),
                'creator_handle': post.get('creator_handle'),
                'profile_url': post.get('profile_url'),
                'title': post.get('title'),
                'caption_text': post.get('caption_text'),
                'hashtags': post.get('hashtags', []),
                'matched_terms': [term for term in terms if term.lower() in haystack],
                'species_terms': post.get('species_terms', []),
                'broad_location': post.get('broad_location', []),
                'media_type': post.get('media_type'),
                'posted_at': post.get('posted_at'),
                'license_status': post.get('license_status'),
                'permission_status': post.get('permission_status'),
                'notes': post.get('notes', '')
            })
            if len(results) >= limit:
                break
    return results

def export_social_leads(sources, rules, output_path):
    '''
    Export social leads CSV.
    '''
    rows = []
    for source in sources:
        cache_dir = get_social_source_cache_dir(rules, source)
        posts_path = cache_dir / 'posts.jsonl'
        if posts_path.exists():
            for post in read_jsonl(posts_path):
                rows.append({
                    'source_id': post.get('source_id'),
                    'platform': post.get('platform'),
                    'source_type': post.get('source_type'),
                    'query': post.get('query'),
                    'creator_handle': post.get('creator_handle'),
                    'creator_display_name': post.get('creator_display_name'),
                    'profile_url': post.get('profile_url'),
                    'post_url': post.get('post_url'),
                    'caption_text': post.get('caption_text'),
                    'hashtags': '|'.join(post.get('hashtags', [])),
                    'species_terms': '|'.join(post.get('species_terms', [])),
                    'broad_location': '|'.join(post.get('broad_location', [])),
                    'media_type': post.get('media_type'),
                    'posted_at': post.get('posted_at'),
                    'engagement_summary': json.dumps(post.get('engagement_summary', {}), sort_keys=True),
                    'contact_path': post.get('contact_path'),
                    'license_status': post.get('license_status'),
                    'permission_status': post.get('permission_status'),
                    'priority': source.get('priority', ''),
                    'notes': source.get('notes', '')
                })
        else:
            rows.append({
                'source_id': source.get('source_id'),
                'platform': source.get('platform'),
                'source_type': source.get('source_type'),
                'query': '',
                'creator_handle': '',
                'creator_display_name': '',
                'profile_url': '',
                'post_url': '',
                'caption_text': '',
                'hashtags': '',
                'species_terms': '',
                'broad_location': '',
                'media_type': 'manual_review',
                'posted_at': '',
                'engagement_summary': '',
                'contact_path': '',
                'license_status': source.get('license_status', ''),
                'permission_status': source.get('permission_status', ''),
                'priority': source.get('priority', ''),
                'notes': source.get('notes', '')
            })
    fieldnames = [
        'source_id', 'platform', 'source_type', 'query', 'creator_handle', 'creator_display_name',
        'profile_url', 'post_url', 'caption_text', 'hashtags', 'species_terms', 'broad_location',
        'media_type', 'posted_at', 'engagement_summary', 'contact_path', 'license_status',
        'permission_status', 'priority', 'notes'
    ]
    write_csv(output_path, fieldnames, rows)
    return {'output': output_path, 'row_count': len(rows)}

def export_social_results(results, output_path):
    '''
    Export social search results CSV.
    '''
    fieldnames = [
        'source_id', 'platform', 'post_url', 'creator_handle', 'profile_url', 'title',
        'caption_text', 'hashtags', 'matched_terms', 'species_terms', 'broad_location',
        'media_type', 'posted_at', 'license_status', 'permission_status', 'notes'
    ]
    rows = []
    for result in results:
        rows.append({
            'source_id': result.get('source_id'),
            'platform': result.get('platform'),
            'post_url': result.get('post_url'),
            'creator_handle': result.get('creator_handle'),
            'profile_url': result.get('profile_url'),
            'title': result.get('title'),
            'caption_text': result.get('caption_text'),
            'hashtags': '|'.join(result.get('hashtags', [])),
            'matched_terms': '|'.join(result.get('matched_terms', [])),
            'species_terms': '|'.join(result.get('species_terms', [])),
            'broad_location': '|'.join(result.get('broad_location', [])),
            'media_type': result.get('media_type'),
            'posted_at': result.get('posted_at'),
            'license_status': result.get('license_status'),
            'permission_status': result.get('permission_status'),
            'notes': result.get('notes', '')
        })
    write_csv(output_path, fieldnames, rows)
    return {'output': output_path, 'row_count': len(rows)}

def load_social_manual_seeds(path):
    '''
    Load manual social review seeds from CSV.
    '''
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))

def import_social_manual_seeds(path, output_dir=DEFAULT_SOCIAL_CACHE_DIR):
    '''
    Import manual social seeds into cache directories.
    '''
    seeds = load_social_manual_seeds(path)
    grouped = {}
    for seed in seeds:
        platform = (seed.get('platform') or 'manual').strip().lower()
        grouped.setdefault(platform, []).append(seed)
    summaries = []
    for platform, rows in grouped.items():
        source_id = f'{platform}_manual_review'
        cache_dir = Path(output_dir) / source_id
        posts = []
        for row in rows:
            query = row.get('query_or_url', '')
            posts.append({
                **build_social_post_record({
                    'source_id': source_id,
                    'platform': platform,
                    'source_type': 'manual_seed',
                    'license_status': 'unknown_contact_required',
                    'permission_status': 'not_requested',
                    'notes': row.get('notes', '')
                }, query=query, title=f'Manual review seed: {query}', caption_text=query, description=row.get('notes', ''), media_type='manual_review', notes=row.get('notes', '')),
                'ingestion_status': 'manual_review_required'
            })
        creators = build_social_creator_records({
            'source_id': source_id,
            'platform': platform,
            'priority': 'medium',
            'notes': 'Imported manual social seeds.'
        }, posts)
        summary = {
            'source_id': source_id,
            'platform': platform,
            'post_count': len(posts),
            'creator_count': len(creators),
            'manual_review_only': True,
            'license_status': 'unknown_contact_required',
            'permission_status': 'not_requested',
            'notes': 'Imported from social_manual_seeds.csv'
        }
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(cache_dir / 'posts.jsonl', posts)
        write_jsonl(cache_dir / 'creators.jsonl', creators)
        write_json(cache_dir / 'source_summary.json', summary)
        summaries.append(summary)
    return {'sources': summaries}

def http_fetch(url, method='GET', timeout=DEFAULT_TIMEOUT_SEC, user_agent=None, headers=None, max_bytes=None):
    '''
    Fetch a URL using stdlib urllib.

    Output:
        dict with url, method, status_code, headers, body, elapsed_sec.
    '''
    req_headers = dict(headers or {})
    if user_agent:
        req_headers.setdefault('User-Agent', user_agent)

    req = request.Request(url, method=method, headers=req_headers)
    start = time.time()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = b'' if method == 'HEAD' else resp.read(max_bytes or -1)
            return {
                'url': url,
                'final_url': resp.geturl(),
                'method': method,
                'status_code': resp.status,
                'headers': dict(resp.headers),
                'body': body,
                'elapsed_sec': round(time.time() - start, 3)
            }
    except error.HTTPError as exc:
        body = b''
        try:
            body = exc.read(max_bytes or 8192)
        except Exception:
            pass
        return {
            'url': url,
            'final_url': exc.geturl() if hasattr(exc, 'geturl') else url,
            'method': method,
            'status_code': exc.code,
            'headers': dict(exc.headers),
            'body': body,
            'elapsed_sec': round(time.time() - start, 3),
            'error': str(exc)
        }
    except Exception as exc:
        raise RequestError(url, str(exc)) from exc

def classify_auth(status_code, configured_auth):
    '''
    Infer credential requirement from configured auth and HTTP status.
    '''
    if configured_auth.get('required'):
        return 'required_by_config'
    if status_code in (401,):
        return 'required_http_401'
    if status_code in (403,):
        return 'possibly_required_or_blocked_http_403'
    return 'not_required_for_checked_url'

def build_creds_template(config, output_path):
    '''
    Build a local credentials template file for all configured databases.
    '''
    payload = {
        'version': config.get('version', '0.1.0'),
        'description': 'Local credentials template. Do not commit this file.',
        'databases': {}
    }

    for db in config.get('databases', []):
        auth = db.get('auth', {'type': 'none', 'required': False})
        db_id = db.get('id')
        payload['databases'][db_id] = {
            'auth_type': auth.get('type', 'none'),
            'required': bool(auth.get('required', False)),
            'headers': {},
            'env': {}
        }
        if auth.get('type') in ('api_key', 'bearer_token') or auth.get('required'):
            env_name = f'{safe_filename(db_id).upper()}_TOKEN'
            payload['databases'][db_id]['env']['token'] = env_name
            payload['databases'][db_id]['headers']['Authorization'] = f'Bearer ${{{env_name}}}'

    existing = {}
    if os.path.exists(output_path):
        try:
            existing = load_json(output_path)
        except ConfigError:
            existing = {}

    for key, default_value in SOCIAL_CREDENTIAL_KEYS.items():
        existing_value = existing.get(key, '')
        payload[key] = existing_value if existing_value not in (None, '') else default_value

    write_json(output_path, payload)
    return payload

def expand_credential_headers(db, creds):
    '''
    Return request headers from creds config with environment-variable expansion.
    '''
    db_creds = creds.get('databases', {}).get(db.get('id'), {}) if creds else {}
    headers = dict(db_creds.get('headers', {}))
    expanded = {}

    for key, value in headers.items():
        env_matches = re.findall(r'\$\{([^}]+)\}', value)
        for env_name in env_matches:
            value = value.replace(f'${{{env_name}}}', os.environ.get(env_name, ''))
        if value and not value.endswith('Bearer '):
            expanded[key] = value

    return expanded

def get_check_urls(db):
    '''
    Return URLs worth checking for a database.
    '''
    urls = []
    for key in ('homepage_url', 'catalog_url', 'metadata_url', 'bbox_url', 'image_base_url'):
        if db.get(key):
            urls.append((key, db[key]))
    for key in ('metadata_urls',):
        for url in db.get(key, []):
            urls.append((key, url))
    return urls

def check_database(db, defaults, creds=None):
    '''
    Access one database and ascertain whether credentials are required.
    '''
    headers = expand_credential_headers(db, creds)
    result = {
        'id': db.get('id'),
        'name': db.get('name'),
        'adapter': db.get('adapter'),
        'configured_auth': db.get('auth', {'type': 'none', 'required': False}),
        'checks': []
    }

    for label, url in get_check_urls(db):
        method = 'HEAD'
        if db.get('adapter') in ('web_page', 'web_download') and label == 'homepage_url':
            method = 'GET'
        try:
            resp = http_fetch(
                url,
                method=method,
                timeout=defaults['timeout_sec'],
                user_agent=defaults['user_agent'],
                headers=headers,
                max_bytes=MAX_TEXT_BYTES)
        except RequestError as exc:
            result['checks'].append({
                'label': label,
                'url': url,
                'ok': False,
                'error': exc.message
            })
            continue

        auth_state = classify_auth(resp['status_code'], db.get('auth', {}))
        result['checks'].append({
            'label': label,
            'url': url,
            'ok': 200 <= resp['status_code'] < 400,
            'status_code': resp['status_code'],
            'auth_state': auth_state,
            'content_type': resp['headers'].get('Content-Type'),
            'content_length': resp['headers'].get('Content-Length'),
            'elapsed_sec': resp['elapsed_sec']
        })

    return result

def summarize_scan(scan):
    '''
    Add a compact connection summary to a database scan result.
    '''
    checks = scan.get('checks', [])
    required_checks = [item for item in checks if item.get('label') != 'image_base_url'] or checks
    reachable = [item for item in checks if item.get('ok')]
    reachable_required = [item for item in required_checks if item.get('ok')]
    connectable = bool(required_checks) and len(reachable_required) == len(required_checks)
    scan['summary'] = {
        'checked_urls': len(checks),
        'reachable_urls': len(reachable),
        'required_urls': len(required_checks),
        'reachable_required_urls': len(reachable_required),
        'connectable': connectable
    }
    return scan

def download_to_cache(url, cache_dir, timeout, user_agent, headers=None, force=False):
    '''
    Download a URL to cache and return the local path.
    '''
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    filename = safe_filename(Path(parsed.path).name or parsed.netloc)
    path = Path(cache_dir) / filename

    if path.exists() and not force:
        log.info(f'Using cached file: {path}')
        return str(path)

    log.info(f'Downloading: {url}')
    resp = http_fetch(url, 'GET', timeout, user_agent, headers=headers)
    if not (200 <= resp['status_code'] < 400):
        raise RequestError(url, f'Download failed: HTTP {resp["status_code"]}')

    with open(path, 'wb') as f:
        f.write(resp['body'])
    return str(path)

def read_zip_json(path):
    '''
    Read the first JSON file from a zip archive.
    '''
    with zipfile.ZipFile(path, 'r') as zf:
        json_names = [name for name in zf.namelist() if name.lower().endswith('.json')]
        if not json_names:
            raise ConfigError(f'No JSON files found in zip: {path}')
        with zf.open(json_names[0]) as f:
            return json.load(f)

def load_local_metadata(db, cache_dir):
    '''
    Load COCO metadata from cache if present.
    '''
    metadata_url = db.get('metadata_url')
    if not metadata_url:
        raise ConfigError(f'Database has no metadata_url: {db.get("id")}')

    parsed = urlparse(metadata_url)
    filename = safe_filename(Path(parsed.path).name or parsed.netloc)
    path = Path(cache_dir) / filename
    if not path.exists():
        raise ConfigError(f'Metadata not cached for {db.get("id")}. Run with --download-metadata first.')

    if path.suffix.lower() == '.zip':
        return read_zip_json(str(path))
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_image_metadata_record(db, metadata, image):
    '''
    Build a metadata record for one COCO image with joined annotations.
    '''
    categories = metadata.get('categories', [])
    annotations = metadata.get('annotations', [])
    licenses = metadata.get('licenses', [])
    categories_by_id = {item.get('id'): item for item in categories}
    licenses_by_id = {item.get('id'): item for item in licenses}

    joined_annotations = []
    for annotation in annotations:
        if annotation.get('image_id') != image.get('id'):
            continue
        joined = dict(annotation)
        if annotation.get('category_id') in categories_by_id:
            joined['category'] = categories_by_id[annotation.get('category_id')]
        joined_annotations.append(joined)

    record = {
        'db': db.get('id'),
        'db_name': db.get('name'),
        'adapter': db.get('adapter'),
        'image_filename': image.get('file_name'),
        'image': image,
        'annotations': joined_annotations,
        'annotation_count': len(joined_annotations)
    }

    if db.get('image_base_url') and image.get('file_name'):
        record['image_url'] = db.get('image_base_url').rstrip('/') + '/' + image.get('file_name').lstrip('/')
    if image.get('license') in licenses_by_id:
        record['license'] = licenses_by_id[image.get('license')]

    return record

def extract_image_metadata(db, defaults, image_name):
    '''
    Extract all available metadata for one image filename from cached COCO metadata.
    '''
    if db.get('adapter') != 'lila_coco_zip':
        raise ConfigError(f'Metadata extraction is only supported for cached COCO datasets: {db.get("id")}')

    metadata = load_local_metadata(db, defaults['cache_dir'])
    needle = image_name.lower()

    for image in metadata.get('images', []):
        file_name = image.get('file_name', '')
        if file_name.lower() == needle or Path(file_name).name.lower() == needle:
            return build_image_metadata_record(db, metadata, image)

    raise ConfigError(f'Image not found in cached metadata for {db.get("id")}: {image_name}')

def summarize_coco_metadata(metadata):
    '''
    Summarize COCO Camera Traps metadata.
    '''
    images = metadata.get('images', [])
    annotations = metadata.get('annotations', [])
    categories = metadata.get('categories', [])

    sample_image_keys = sorted(images[0].keys()) if images else []
    sample_annotation_keys = sorted(annotations[0].keys()) if annotations else []
    sample_category_keys = sorted(categories[0].keys()) if categories else []

    return {
        'format': 'COCO Camera Traps JSON',
        'top_level_keys': sorted(metadata.keys()),
        'counts': {
            'images': len(images),
            'annotations': len(annotations),
            'categories': len(categories)
        },
        'sample_keys': {
            'image': sample_image_keys,
            'annotation': sample_annotation_keys,
            'category': sample_category_keys
        },
        'categories': categories[:100]
    }

def map_lila_coco(db, defaults, creds, download_metadata=False, force=False):
    '''
    Map a LILA COCO zip dataset.
    '''
    headers = expand_credential_headers(db, creds)
    mapped = {
        'id': db.get('id'),
        'name': db.get('name'),
        'adapter': db.get('adapter'),
        'urls': {key: db.get(key) for key in ('homepage_url', 'image_base_url', 'metadata_url', 'bbox_url') if db.get(key)},
        'auth': db.get('auth', {}),
        'api_surface': {
            'kind': 'static_public_dataset',
            'query_api': False,
            'metadata_format': 'COCO Camera Traps JSON',
            'operations': [
                'GET homepage_url',
                'GET metadata_url',
                'GET bbox_url if present',
                'GET image_base_url + image.file_name when metadata is cached'
            ]
        }
    }

    checks = check_database(db, defaults, creds)
    mapped['access_checks'] = checks['checks']

    if download_metadata:
        local_path = download_to_cache(
            db['metadata_url'],
            defaults['cache_dir'],
            defaults['timeout_sec'],
            defaults['user_agent'],
            headers=headers,
            force=force)
        metadata = read_zip_json(local_path) if local_path.endswith('.zip') else load_json(local_path)
        mapped['metadata_summary'] = summarize_coco_metadata(metadata)
        mapped['cache_path'] = local_path
    else:
        mapped['metadata_summary'] = {
            'status': 'not_loaded',
            'hint': 'Run --map --download-metadata to cache and inspect metadata keys/counts.'
        }

    return mapped

def map_lila_catalog(db, defaults, creds):
    '''
    Map the LILA catalog CSV.
    '''
    headers = expand_credential_headers(db, creds)
    local_path = download_to_cache(
        db['catalog_url'],
        defaults['cache_dir'],
        defaults['timeout_sec'],
        defaults['user_agent'],
        headers=headers)

    with open(local_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    return {
        'id': db.get('id'),
        'name': db.get('name'),
        'adapter': db.get('adapter'),
        'urls': {'homepage_url': db.get('homepage_url'), 'catalog_url': db.get('catalog_url')},
        'api_surface': {
            'kind': 'static_public_catalog_csv',
            'query_api': False,
            'operations': ['GET catalog_url', 'filter CSV rows locally']
        },
        'csv_summary': {
            'columns': reader.fieldnames,
            'rows': len(rows),
            'sample_rows': rows[:5]
        },
        'cache_path': local_path,
        'access_checks': check_database(db, defaults, creds)['checks']
    }

def strip_html(html):
    '''
    Basic HTML stripper for simple page search/mapping.
    '''
    text = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', ' ', html, flags=re.I)
    text = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def map_web_page(db, defaults, creds):
    '''
    Map a web page/download source.
    '''
    headers = expand_credential_headers(db, creds)
    resp = http_fetch(
        db['homepage_url'],
        'GET',
        defaults['timeout_sec'],
        defaults['user_agent'],
        headers=headers,
        max_bytes=MAX_TEXT_BYTES)
    body = resp.get('body', b'').decode('utf-8', errors='replace')
    links = sorted(set(re.findall(r'href=["\']([^"\']+)', body, flags=re.I)))[:200]

    return {
        'id': db.get('id'),
        'name': db.get('name'),
        'adapter': db.get('adapter'),
        'urls': {'homepage_url': db.get('homepage_url'), 'metadata_urls': db.get('metadata_urls', [])},
        'api_surface': {
            'kind': 'public_web_download_page',
            'query_api': False,
            'operations': ['GET homepage_url', 'manual/browser-mediated project downloads where exposed']
        },
        'page_summary': {
            'status_code': resp['status_code'],
            'content_type': resp['headers'].get('Content-Type'),
            'links_sample': links,
            'text_sample': strip_html(body)[:2000]
        },
        'access_checks': check_database(db, defaults, creds)['checks']
    }

def map_database(db, defaults, creds=None, download_metadata=False, force=False):
    '''
    Map the accessible surface for one database.
    '''
    adapter = db.get('adapter')

    if adapter == 'lila_coco_zip':
        return map_lila_coco(db, defaults, creds, download_metadata, force)
    if adapter == 'lila_catalog_csv':
        return map_lila_catalog(db, defaults, creds)
    if adapter in ('web_page', 'web_download'):
        return map_web_page(db, defaults, creds)

    return {
        'id': db.get('id'),
        'name': db.get('name'),
        'adapter': adapter,
        'api_surface': {'kind': 'unknown_adapter', 'query_api': False},
        'access_checks': check_database(db, defaults, creds)['checks']
    }

def list_database_api(db, defaults, creds=None, download_metadata=False, force=False):
    '''
    Return the mapped API/static-data surface for one database in list form.
    '''
    mapped = map_database(db, defaults, creds, download_metadata, force)
    api_surface = mapped.get('api_surface', {})
    return {
        'id': mapped.get('id'),
        'name': mapped.get('name'),
        'adapter': mapped.get('adapter'),
        'api_surface': api_surface,
        'operations': api_surface.get('operations', []),
        'access_checks': mapped.get('access_checks', [])
    }

def text_matches(value, terms):
    '''
    Return True if all terms are found in value text.
    '''
    haystack = json.dumps(value, sort_keys=True).lower()
    return all(term.lower() in haystack for term in terms)

def find_in_lila_catalog(db, defaults, creds, terms, limit):
    '''
    Search LILA catalog rows.
    '''
    headers = expand_credential_headers(db, creds)
    local_path = download_to_cache(db['catalog_url'], defaults['cache_dir'], defaults['timeout_sec'], defaults['user_agent'], headers=headers)
    results = []

    with open(local_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if text_matches(row, terms):
                results.append(row)
                if len(results) >= limit:
                    break

    return results

def find_in_lila_coco(db, defaults, terms, limit):
    '''
    Search cached LILA COCO metadata by category, image, or annotation text.
    '''
    metadata = load_local_metadata(db, defaults['cache_dir'])
    categories = metadata.get('categories', [])
    category_by_id = {cat.get('id'): cat for cat in categories}
    results = []

    for cat in categories:
        if text_matches(cat, terms):
            results.append({'kind': 'category', 'db': db.get('id'), 'category': cat})
            if len(results) >= limit:
                return results

    for image in metadata.get('images', []):
        if text_matches(image, terms):
            item = {'kind': 'image', 'db': db.get('id'), 'image': image}
            if db.get('image_base_url') and image.get('file_name'):
                item['image_url'] = db['image_base_url'].rstrip('/') + '/' + image['file_name'].lstrip('/')
            results.append(item)
            if len(results) >= limit:
                return results

    for annotation in metadata.get('annotations', []):
        joined = dict(annotation)
        if annotation.get('category_id') in category_by_id:
            joined['category'] = category_by_id[annotation['category_id']]
        if text_matches(joined, terms):
            results.append({'kind': 'annotation', 'db': db.get('id'), 'annotation': joined})
            if len(results) >= limit:
                return results

    return results

def find_in_web_page(db, defaults, creds, terms, limit):
    '''
    Search a public web page or small metadata URLs.
    '''
    headers = expand_credential_headers(db, creds)
    urls = [db.get('homepage_url')] + db.get('metadata_urls', [])
    results = []

    for url in [u for u in urls if u]:
        resp = http_fetch(url, 'GET', defaults['timeout_sec'], defaults['user_agent'], headers=headers, max_bytes=MAX_TEXT_BYTES)
        body = resp.get('body', b'').decode('utf-8', errors='replace')
        text = strip_html(body)
        lower = text.lower()

        for term in terms:
            idx = lower.find(term.lower())
            if idx >= 0:
                start = max(0, idx - 250)
                end = min(len(text), idx + 500)
                results.append({
                    'kind': 'web_text_match',
                    'db': db.get('id'),
                    'url': url,
                    'term': term,
                    'snippet': text[start:end]
                })
                if len(results) >= limit:
                    return results

    return results

def find_database(db, defaults, creds, terms, limit):
    '''
    Search one database using its adapter.
    '''
    adapter = db.get('adapter')

    if adapter == 'lila_catalog_csv':
        return find_in_lila_catalog(db, defaults, creds, terms, limit)
    if adapter == 'lila_coco_zip':
        return find_in_lila_coco(db, defaults, terms, limit)
    if adapter in ('web_page', 'web_download'):
        return find_in_web_page(db, defaults, creds, terms, limit)

    return []

# ****************************************************************************************
# Handle the arguments
# ****************************************************************************************

def handle_args():
    '''
    Parse CLI arguments and configure console logging handlers.
    '''
    parser = argparse.ArgumentParser(description='Trail camera open database utilities')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output to stdout.')
    parser.add_argument('-q', '--quiet', action='store_true', help='Minimal stdout.')
    parser.add_argument('--db-config', default=DEFAULT_DB_CONFIG, help='Path to open trail camera DB config JSON.')
    parser.add_argument('--creds-config', default=DEFAULT_CREDS_CONFIG, help='Path to local credentials config JSON.')
    parser.add_argument('--personal-sources', default=DEFAULT_PERSONAL_SOURCES_CONFIG, help='Path to personal/small-collection sources JSON.')
    parser.add_argument('--personal-source', help='Personal source id, or "all" for every source.')
    parser.add_argument('--social-sources', default=DEFAULT_SOCIAL_SOURCES_CONFIG, help='Path to social discovery sources JSON.')
    parser.add_argument('--social-source', help='Social source id, or "all" for every source.')
    parser.add_argument('--social-manual-seeds', default=DEFAULT_SOCIAL_MANUAL_SEEDS, help='Path to social manual review seeds CSV.')
    parser.add_argument('--db', help='Database id to operate on. Default is all databases.')
    parser.add_argument('--init-creds', action='store_true', help='Create a local credentials template and exit.')
    parser.add_argument('--check', action='store_true', help='Access configured DB URLs and determine credential requirements.')
    parser.add_argument('--validate-personal-config', action='store_true', help='Validate the personal-source config file.')
    parser.add_argument('--check-personal', action='store_true', help='Check entry-URL accessibility for configured personal sources.')
    parser.add_argument('--discover', action='store_true', help='Discover and cache metadata for configured personal sources.')
    parser.add_argument('--validate-social-config', action='store_true', help='Validate the social discovery config file.')
    parser.add_argument('--check-social', action='store_true', help='Check readiness for configured social discovery sources.')
    parser.add_argument('--discover-social', action='store_true', help='Discover and cache metadata for configured social sources.')
    parser.add_argument('--import-manual-seeds', action='store_true', help='Import manual social review seeds from CSV.')
    parser.add_argument('--scan-dbs', action='store_true', help='Check every configured database for reachability and connection readiness.')
    parser.add_argument('--map', action='store_true', help='Map the accessible API/static-data surface for configured DBs.')
    parser.add_argument('--list-api', metavar='DB', help='Map one database and list its available API/static-data operations.')
    parser.add_argument('--metadata-extract', metavar='IMAGE', help='Extract cached metadata for one image filename from a COCO dataset selected with --db.')
    parser.add_argument('--download-metadata', action='store_true', help='Download metadata where supported before mapping/searching.')
    parser.add_argument('--force-download', action='store_true', help='Re-download metadata even if cached.')
    parser.add_argument('--find', nargs='+', help='Search terms. All terms must match within an item for structured sources.')
    parser.add_argument('--limit', type=int, default=20, help='Maximum search results per database.')
    parser.add_argument('--export-leads', help='Write personal source leads to this CSV path.')
    parser.add_argument('--export-results', help='Write personal search results to this CSV path.')
    parser.add_argument('--export-social-leads', help='Write social discovery leads to this CSV path.')
    parser.add_argument('--export-social-results', help='Write social search results to this CSV path.')
    parser.add_argument('--output', help='Write JSON output to this path. Defaults to stdout for check/find and api map files for --map.')
    args = parser.parse_args()

    # Configure stdout logging based on arguments
    ch = logging.StreamHandler(sys.stdout)
    if args.verbose:
        ch.setLevel(logging.DEBUG)
    elif args.quiet:
        ch.setLevel(logging.ERROR)
    else:
        ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    log.addHandler(ch)

    log.debug('Checking script requirements...')
    if not any([
        args.init_creds, args.check, args.validate_personal_config, args.check_personal,
        args.discover, args.validate_social_config, args.check_social, args.discover_social,
        args.import_manual_seeds, args.scan_dbs, args.map, args.list_api, args.metadata_extract,
        args.find, args.export_leads, args.export_social_leads
    ]):
        parser.error('Choose one action: --init-creds, --check, --validate-personal-config, --check-personal, --discover, --validate-social-config, --check-social, --discover-social, --import-manual-seeds, --scan-dbs, --map, --list-api, --metadata-extract, --export-leads, --export-social-leads, or --find')
    if args.metadata_extract and not args.db:
        parser.error('--metadata-extract requires --db')
    if args.personal_source and not any([args.find]):
        log.debug('Ignoring --personal-source without a personal search action.')

    log.info('++++++++++++++++++++++++++++++++++++++++++++++')
    log.info(f'+  {SCRIPT_NAME}')
    log.info(f'+  Python Version: {sys.version.split()[0]}')
    log.info(f'+  Today is: {date.today()}')
    log.info('++++++++++++++++++++++++++++++++++++++++++++++')

    return args

# ****************************************************************************************
# Main
# ****************************************************************************************

def main():
    '''
    Entrypoint.
    '''
    args = handle_args()
    social_mode = any([args.validate_social_config, args.check_social, args.discover_social, args.export_social_leads, args.import_manual_seeds]) or (args.social_source and args.find)
    personal_mode = any([args.validate_personal_config, args.check_personal, args.discover, args.export_leads]) or (args.personal_source and args.find)

    if social_mode:
        social_config = load_social_sources_config(args.social_sources)
        validation = validate_social_sources_config(social_config)
        social_defaults = get_social_defaults(social_config)
        sources = get_social_sources(social_config, args.social_source)
        creds = {}
        if os.path.exists(args.creds_config):
            creds = load_json(args.creds_config)

        if args.validate_social_config:
            if args.output:
                write_json(args.output, validation)
            else:
                print(json.dumps(validation, indent=2, sort_keys=True))
            return

        if args.check_social:
            payload = {'sources': [get_social_adapter(source).check(source, creds, social_defaults) for source in sources]}
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

        if args.discover_social:
            payload = {'sources': [get_social_adapter(source).discover(source, creds, social_defaults) for source in sources]}
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

        if args.import_manual_seeds:
            payload = import_social_manual_seeds(args.social_manual_seeds, social_defaults['cache_dir'])
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

        if args.export_social_leads:
            payload = export_social_leads(sources, social_defaults, args.export_social_leads)
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

        if args.find and args.social_source:
            payload = {'terms': args.find, 'sources': []}
            flat_results = []
            for source in sources:
                try:
                    results = get_social_adapter(source).search_cache(source, social_defaults, args.find, args.limit)
                except ConfigError as exc:
                    results = [{'error': str(exc)}]
                payload['sources'].append({
                    'source_id': source.get('source_id'),
                    'platform': source.get('platform'),
                    'result_count': len(results),
                    'results': results
                })
                flat_results.extend(result for result in results if 'error' not in result)
            if args.export_social_results:
                export_social_results(flat_results, args.export_social_results)
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

    if personal_mode:
        personal_config = load_personal_sources_config(args.personal_sources)
        validation = validate_personal_sources_config(personal_config)
        personal_defaults = get_personal_defaults(personal_config)
        sources = get_personal_sources(personal_config, args.personal_source)

        if args.validate_personal_config:
            if args.output:
                write_json(args.output, validation)
            else:
                print(json.dumps(validation, indent=2, sort_keys=True))
            return

        if args.check_personal:
            payload = {'sources': [get_personal_adapter(source).check(source, personal_defaults) for source in sources]}
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

        if args.discover:
            payload = {'sources': [get_personal_adapter(source).discover(source, personal_defaults) for source in sources]}
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

        if args.export_leads:
            payload = export_personal_leads(sources, personal_defaults, args.export_leads)
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

        if args.find and args.personal_source:
            payload = {'terms': args.find, 'sources': []}
            flat_results = []
            for source in sources:
                try:
                    results = get_personal_adapter(source).search(source, personal_defaults, args.find, args.limit)
                except ConfigError as exc:
                    results = [{'error': str(exc)}]
                payload['sources'].append({
                    'source_id': source.get('source_id'),
                    'display_name': source.get('display_name'),
                    'result_count': len(results),
                    'results': results
                })
                flat_results.extend(result for result in results if 'error' not in result)
            if args.export_results:
                export_personal_results(flat_results, args.export_results)
            if args.output:
                write_json(args.output, payload)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return

    config = load_json(args.db_config)
    defaults = get_defaults(config)

    creds = {}
    if os.path.exists(args.creds_config):
        creds = load_json(args.creds_config)

    if args.init_creds:
        payload = build_creds_template(config, args.creds_config)
        log.info(f'Wrote credentials template: {args.creds_config}')
        if args.output:
            write_json(args.output, payload)
        return

    databases = get_databases(config, args.db)

    if args.check:
        payload = {'databases': [check_database(db, defaults, creds) for db in databases]}
        if args.output:
            write_json(args.output, payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if args.scan_dbs:
        payload = {'databases': [summarize_scan(check_database(db, defaults, creds)) for db in databases]}
        if args.output:
            write_json(args.output, payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if args.map:
        maps = []
        for db in databases:
            mapped = map_database(db, defaults, creds, args.download_metadata, args.force_download)
            maps.append(mapped)
            if not args.output:
                out_dir = Path(defaults['api_map_dir'])
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f'{safe_filename(db.get("id", "db"))}.api_map.json'
                write_json(out_path, mapped)
                log.info(f'Wrote API map: {out_path}')
        if args.output:
            write_json(args.output, {'databases': maps})
        return

    if args.list_api:
        db = get_databases(config, args.list_api)[0]
        payload = list_database_api(db, defaults, creds, args.download_metadata, args.force_download)
        if args.output:
            write_json(args.output, payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if args.metadata_extract:
        db = get_databases(config, args.db)[0]
        payload = extract_image_metadata(db, defaults, args.metadata_extract)
        if args.output:
            out_path = args.output
        else:
            out_path = f'{safe_filename(db.get("id", "db"))}_{safe_filename(Path(args.metadata_extract).name)}.metadata.json'
        write_json(out_path, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        if not args.quiet:
            log.info(f'Wrote metadata extract: {out_path}')
        return

    if args.find:
        payload = {'terms': args.find, 'databases': []}
        for db in databases:
            if args.download_metadata and db.get('adapter') == 'lila_coco_zip':
                headers = expand_credential_headers(db, creds)
                download_to_cache(
                    db['metadata_url'],
                    defaults['cache_dir'],
                    defaults['timeout_sec'],
                    defaults['user_agent'],
                    headers=headers,
                    force=args.force_download)
            try:
                results = find_database(db, defaults, creds, args.find, args.limit)
            except ConfigError as exc:
                results = [{'error': str(exc)}]
            payload['databases'].append({
                'id': db.get('id'),
                'name': db.get('name'),
                'adapter': db.get('adapter'),
                'result_count': len(results),
                'results': results
            })
        if args.output:
            write_json(args.output, payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return

if __name__ == '__main__':
    main()
