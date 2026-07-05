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
import hashlib
import http.server
from html.parser import HTMLParser
import json
import logging
import math
import os
import re
import socketserver
import sqlite3
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
DEFAULT_COVERAGE_DIR = './trailcam_coverage'
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
LOCATION_RESOLVER = {
    'oley, pa': {'lat': 40.3876, 'lon': -75.7894, 'precision': 'site_declared_region', 'state': 'PA', 'county': 'Berks County', 'place': 'Oley'},
    'oley valley': {'lat': 40.38, 'lon': -75.79, 'precision': 'site_declared_region', 'state': 'PA', 'county': 'Berks County', 'place': 'Oley Valley'},
    'berks county, pa': {'lat': 40.3452, 'lon': -75.9928, 'precision': 'county_centroid', 'state': 'PA', 'county': 'Berks County', 'place': 'Berks County'},
    'reading, pa': {'lat': 40.3356, 'lon': -75.9269, 'precision': 'site_declared_region', 'state': 'PA', 'county': 'Berks County', 'place': 'Reading'},
    'pennsylvania': {'lat': 40.9699, 'lon': -77.7279, 'precision': 'state_centroid', 'state': 'PA'},
    'pa': {'lat': 40.9699, 'lon': -77.7279, 'precision': 'state_centroid', 'state': 'PA'},
    'philadelphia': {'lat': 39.9526, 'lon': -75.1652, 'precision': 'site_declared_region', 'state': 'PA', 'county': 'Philadelphia County', 'place': 'Philadelphia'},
    'colorado front range': {'lat': 39.55, 'lon': -105.15, 'precision': 'site_declared_region', 'state': 'CO'},
    'northern california': {'lat': 40.0, 'lon': -122.0, 'precision': 'site_declared_region', 'state': 'CA'},
    'florida': {'lat': 27.6648, 'lon': -81.5158, 'precision': 'state_centroid', 'state': 'FL'},
    'alaska': {'lat': 64.2008, 'lon': -149.4937, 'precision': 'state_centroid', 'state': 'AK'},
    'ontario': {'lat': 50.0, 'lon': -85.0, 'precision': 'state_centroid', 'state': 'Ontario', 'country': 'CA'}
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

def utc_today():
    '''
    Return today's date string.
    '''
    return date.today().isoformat()

def stable_id(*parts):
    '''
    Build a stable identifier from text parts.
    '''
    joined = '|'.join(str(part or '') for part in parts)
    return hashlib.sha1(joined.encode('utf-8')).hexdigest()[:20]

def resolve_broad_location(location_text):
    '''
    Resolve a broad location from static known regions.
    '''
    if not location_text:
        return {
            'latitude': None, 'longitude': None, 'coordinate_precision': 'unknown',
            'admin_country': '', 'admin_state': '', 'admin_county': '', 'admin_place': '',
            'location_label': '', 'confidence_score': 0.0
        }
    lower = location_text.lower()
    for key, value in LOCATION_RESOLVER.items():
        if key in lower:
            return {
                'latitude': value.get('lat'),
                'longitude': value.get('lon'),
                'coordinate_precision': value.get('precision', 'unknown'),
                'admin_country': value.get('country', 'US' if value.get('state') else ''),
                'admin_state': value.get('state', ''),
                'admin_county': value.get('county', ''),
                'admin_place': value.get('place', ''),
                'location_label': location_text,
                'confidence_score': 0.55 if value.get('precision') == 'county_centroid' else 0.45
            }
    return {
        'latitude': None, 'longitude': None, 'coordinate_precision': 'unknown',
        'admin_country': '', 'admin_state': '', 'admin_county': '', 'admin_place': '',
        'location_label': location_text, 'confidence_score': 0.0
    }

def haversine_miles(lat1, lon1, lat2, lon2):
    '''
    Compute haversine distance in miles.
    '''
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 3958.8
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

def calculate_deployment_confidence(record):
    '''
    Score one normalized deployment record.
    '''
    precision = record.get('coordinate_precision', 'unknown')
    score = 0.0
    if precision == 'exact_public':
        score += 0.45
    elif precision == 'fuzzed_public':
        score += 0.35
    elif precision in ('project_centroid', 'park_or_public_land_centroid'):
        score += 0.25
    elif precision == 'county_centroid':
        score += 0.15
    elif precision == 'state_centroid':
        score += 0.08
    elif precision in ('site_declared_region', 'creator_profile_region', 'hashtag_region'):
        score += 0.10
    elif precision == 'location_id_only':
        score += 0.05
    image_count = int(record.get('image_count') or 0)
    if image_count >= 1000:
        score += 0.20
    elif image_count >= 100:
        score += 0.12
    elif image_count > 0:
        score += 0.05
    camera_days = record.get('camera_days')
    if camera_days and camera_days >= 30:
        score += 0.15
    elif camera_days and camera_days > 0:
        score += 0.05
    if record.get('species_terms'):
        score += 0.10
    if record.get('license_status') in ('public_domain_confirmed', 'creative_commons_confirmed', 'permission_obtained'):
        score += 0.10
    return min(score, 1.0)

def calculate_direct_fov_area(range_ft=60, fov_degrees=50, obstruction_factor=0.5):
    '''
    Calculate aggregate field-of-view estimates.
    '''
    sector_area_sqft = math.pi * (range_ft ** 2) * (fov_degrees / 360.0)
    effective_area_sqft = sector_area_sqft * obstruction_factor
    return {
        'sector_area_sqft': sector_area_sqft,
        'effective_area_sqft': effective_area_sqft,
        'effective_area_acres': effective_area_sqft / 43560.0
    }

def merge_species_blobs(blob_text):
    '''
    Merge concatenated JSON species arrays into a flat sorted list.
    '''
    terms = set()
    for blob in (blob_text or '').split('|'):
        blob = blob.strip()
        if not blob:
            continue
        try:
            values = json.loads(blob)
        except json.JSONDecodeError:
            values = []
        for term in values:
            if term:
                terms.add(term)
    return sorted(terms)

def safe_json_loads(blob, fallback):
    '''
    Load JSON blobs defensively.
    '''
    if blob in (None, ''):
        return fallback
    try:
        return json.loads(blob)
    except (TypeError, json.JSONDecodeError):
        return fallback

def priority_for_lead_score(score):
    '''
    Map normalized lead score to a simple priority bucket.
    '''
    if score >= 0.75:
        return 'high'
    if score >= 0.45:
        return 'medium'
    return 'low'

def score_creator_lead(record, target_place=None):
    '''
    Score one creator/account lead on a normalized 0-1 scale.
    '''
    text_parts = [
        record.get('creator_handle', ''), record.get('creator_display_name', ''),
        record.get('location_label', ''), record.get('notes', ''), record.get('platform', ''),
        record.get('source_type', ''), target_place or ''
    ]
    species_terms = record.get('species_terms', []) or []
    text_parts.extend(species_terms)
    text = ' '.join(str(part).lower() for part in text_parts if part)
    score = 0.0
    if any(term in text for term in ('trailcam', 'trail cam', 'trail camera', 'game cam', 'camera trap')):
        score += 3
    if any(term in text for term in ('oley', 'berks', 'reading', 'pennsylvania', 'pa wildlife', 'pawildlife')):
        score += 3
    post_count = int(record.get('post_count_seen') or 0)
    if post_count >= 5:
        score += 3
    elif post_count >= 2:
        score += 2
    elif post_count >= 1:
        score += 1
    if record.get('contact_path'):
        score += 2
    if species_terms:
        score += 2
    if any(species in species_terms for species in ('bobcat', 'coyote', 'bear', 'deer', 'turkey', 'fox', 'raccoon', 'person', 'vehicle')):
        score += 2
    platform = (record.get('platform') or '').lower()
    if platform == 'youtube':
        score += 2
    elif platform in ('instagram', 'tiktok', 'facebook'):
        score += 1
    return min(score / 15.0, 1.0)

def init_location_index(db_path):
    '''
    Initialize the location index schema.
    '''
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS camera_sources (
        source_id TEXT PRIMARY KEY, source_type TEXT, platform TEXT, display_name TEXT,
        base_url TEXT, source_config_path TEXT, license_status TEXT, permission_status TEXT,
        priority TEXT, notes TEXT, created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS camera_deployments (
        deployment_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, dataset_id TEXT, location_id TEXT,
        latitude REAL, longitude REAL, coordinate_precision TEXT NOT NULL, coordinate_public INTEGER DEFAULT 0,
        coordinate_obfuscated INTEGER DEFAULT 0, start_date TEXT, end_date TEXT, camera_days REAL,
        image_count INTEGER DEFAULT 0, sequence_count INTEGER DEFAULT 0, video_count INTEGER DEFAULT 0,
        species_count INTEGER DEFAULT 0, species_terms TEXT, habitat_terms TEXT, location_label TEXT,
        admin_country TEXT, admin_state TEXT, admin_county TEXT, admin_place TEXT, source_url TEXT,
        page_url TEXT, license_status TEXT, permission_status TEXT, confidence_score REAL DEFAULT 0,
        created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS camera_observations (
        observation_id TEXT PRIMARY KEY, deployment_id TEXT, source_id TEXT NOT NULL, media_url TEXT,
        page_url TEXT, observed_at TEXT, species_terms TEXT, category_terms TEXT, has_person INTEGER DEFAULT 0,
        has_vehicle INTEGER DEFAULT 0, has_bbox INTEGER DEFAULT 0, sequence_id TEXT, license_status TEXT,
        permission_status TEXT, confidence_score REAL DEFAULT 0, created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS location_signals (
        signal_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, signal_type TEXT, raw_text TEXT, normalized_label TEXT,
        latitude REAL, longitude REAL, coordinate_precision TEXT NOT NULL, admin_country TEXT, admin_state TEXT,
        admin_county TEXT, admin_place TEXT, page_url TEXT, post_url TEXT, creator_id TEXT, species_terms TEXT,
        confidence_score REAL DEFAULT 0, created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS creator_leads (
        lead_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, platform TEXT, creator_handle TEXT, creator_display_name TEXT,
        profile_url TEXT, contact_path TEXT, source_type TEXT, location_label TEXT, admin_state TEXT, admin_county TEXT,
        admin_place TEXT, species_terms TEXT, sample_urls TEXT, post_count_seen INTEGER DEFAULT 0, media_count_seen INTEGER DEFAULT 0,
        video_count_seen INTEGER DEFAULT 0, license_status TEXT, permission_status TEXT DEFAULT 'not_requested',
        review_status TEXT DEFAULT 'candidate', lead_score REAL DEFAULT 0, priority TEXT, notes TEXT, created_at TEXT, updated_at TEXT
    );
    DROP TABLE IF EXISTS coverage_cells;
    CREATE TABLE IF NOT EXISTS coverage_cells (
        cell_id TEXT, grid_type TEXT, resolution INTEGER, source_count INTEGER DEFAULT 0,
        deployment_count INTEGER DEFAULT 0, signal_count INTEGER DEFAULT 0, observation_count INTEGER DEFAULT 0,
        lead_count INTEGER DEFAULT 0, camera_days REAL DEFAULT 0,
        image_count INTEGER DEFAULT 0, sequence_count INTEGER DEFAULT 0, video_count INTEGER DEFAULT 0,
        species_terms TEXT, precision_rollup TEXT, first_seen TEXT, last_seen TEXT, confidence_score REAL DEFAULT 0,
        created_at TEXT, updated_at TEXT, PRIMARY KEY(cell_id, grid_type, resolution)
    );
    CREATE TABLE IF NOT EXISTS coverage_reports (
        report_id TEXT PRIMARY KEY, report_type TEXT, place_name TEXT, latitude REAL, longitude REAL,
        radius_miles REAL, report_path TEXT, summary_json TEXT, created_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_deployments_source_id ON camera_deployments(source_id);
    CREATE INDEX IF NOT EXISTS idx_deployments_lat_lon ON camera_deployments(latitude, longitude);
    CREATE INDEX IF NOT EXISTS idx_deployments_precision ON camera_deployments(coordinate_precision);
    CREATE INDEX IF NOT EXISTS idx_deployments_admin_state ON camera_deployments(admin_state);
    CREATE INDEX IF NOT EXISTS idx_deployments_admin_county ON camera_deployments(admin_county);
    CREATE INDEX IF NOT EXISTS idx_observations_source_id ON camera_observations(source_id);
    CREATE INDEX IF NOT EXISTS idx_observations_deployment_id ON camera_observations(deployment_id);
    CREATE INDEX IF NOT EXISTS idx_location_signals_source_id ON location_signals(source_id);
    CREATE INDEX IF NOT EXISTS idx_location_signals_admin_state ON location_signals(admin_state);
    CREATE INDEX IF NOT EXISTS idx_location_signals_admin_county ON location_signals(admin_county);
    CREATE INDEX IF NOT EXISTS idx_creator_leads_source_id ON creator_leads(source_id);
    CREATE INDEX IF NOT EXISTS idx_creator_leads_review_status ON creator_leads(review_status);
    CREATE INDEX IF NOT EXISTS idx_creator_leads_lead_score ON creator_leads(lead_score);
    CREATE INDEX IF NOT EXISTS idx_cells_resolution ON coverage_cells(grid_type, resolution);
    """)
    conn.commit()
    conn.close()

def normalize_open_db_locations(db_config_path, cache_dir):
    '''
    Normalize location-aware records from open DB configs/caches.
    '''
    records = {'sources': [], 'deployments': [], 'observations': [], 'signals': [], 'leads': []}
    config = load_json(db_config_path)
    for db in config.get('databases', []):
        records['sources'].append({
            'source_id': db.get('id'),
            'source_type': 'open_db',
            'platform': db.get('adapter'),
            'display_name': db.get('name'),
            'base_url': db.get('homepage_url') or db.get('catalog_url') or '',
            'source_config_path': db_config_path,
            'license_status': db.get('auth', {}).get('type', 'unknown'),
            'permission_status': 'not_requested',
            'priority': 'medium',
            'notes': db.get('notes', '')
        })
        if db.get('adapter') == 'lila_coco_zip':
            try:
                metadata = load_local_metadata(db, cache_dir)
            except ConfigError:
                continue
            images = metadata.get('images', [])
            annotations = metadata.get('annotations', [])
            by_loc = {}
            species_by_loc = {}
            image_by_id = {img.get('id'): img for img in images}
            category_by_id = {cat.get('id'): cat.get('name') for cat in metadata.get('categories', [])}
            for img in images:
                loc = str(img.get('location') or img.get('location_id') or 'unknown')
                bucket = by_loc.setdefault(loc, {'image_count': 0, 'video_count': 0, 'sequence_ids': set(), 'sample_image': img})
                bucket['image_count'] += 1
                if img.get('seq_id'):
                    bucket['sequence_ids'].add(str(img.get('seq_id')))
            for ann in annotations:
                img = image_by_id.get(ann.get('image_id'))
                loc = str((img or {}).get('location') or (img or {}).get('location_id') or 'unknown')
                species_by_loc.setdefault(loc, set()).add(category_by_id.get(ann.get('category_id'), ''))
            for loc, bucket in by_loc.items():
                sample = bucket['sample_image']
                deployment = {
                    'deployment_id': stable_id(db.get('id'), loc),
                    'source_id': db.get('id'),
                    'dataset_id': db.get('id'),
                    'location_id': loc,
                    'latitude': None,
                    'longitude': None,
                    'coordinate_precision': 'location_id_only',
                    'coordinate_public': 0,
                    'coordinate_obfuscated': 0,
                    'start_date': sample.get('date_captured') or sample.get('datetime') or '',
                    'end_date': sample.get('date_captured') or sample.get('datetime') or '',
                    'camera_days': None,
                    'image_count': bucket['image_count'],
                    'sequence_count': len(bucket['sequence_ids']),
                    'video_count': 0,
                    'species_count': len(species_by_loc.get(loc, set())),
                    'species_terms': sorted(term for term in species_by_loc.get(loc, set()) if term),
                    'habitat_terms': [],
                    'location_label': loc,
                    'admin_country': '',
                    'admin_state': '',
                    'admin_county': '',
                    'admin_place': '',
                    'source_url': db.get('homepage_url') or '',
                    'page_url': db.get('homepage_url') or '',
                    'license_status': 'unknown_contact_required',
                    'permission_status': 'not_requested'
                }
                deployment['confidence_score'] = calculate_deployment_confidence(deployment)
                records['deployments'].append(deployment)
        else:
            label = db.get('name') or db.get('id')
            resolved = resolve_broad_location(label)
            records['signals'].append({
                'signal_id': stable_id(db.get('id'), label, 'signal'),
                'source_id': db.get('id'),
                'latitude': resolved['latitude'],
                'longitude': resolved['longitude'],
                'signal_type': 'dataset_region',
                'raw_text': label,
                'normalized_label': resolved['location_label'] or label,
                'coordinate_precision': resolved['coordinate_precision'],
                'species_terms': [],
                'admin_country': resolved['admin_country'],
                'admin_state': resolved['admin_state'],
                'admin_county': resolved['admin_county'],
                'admin_place': resolved['admin_place'],
                'page_url': db.get('homepage_url') or '',
                'post_url': db.get('homepage_url') or '',
                'creator_id': '',
                'confidence_score': resolved.get('confidence_score', 0.0)
            })
    return records

def normalize_personal_locations(cache_dir, config_path):
    '''
    Normalize location-aware records from personal-source cache.
    '''
    records = {'sources': [], 'deployments': [], 'observations': [], 'signals': [], 'leads': []}
    config = load_json(config_path)
    for source in config.get('sources', []):
        source_id = source.get('source_id')
        records['sources'].append({
            'source_id': source_id,
            'source_type': 'personal',
            'platform': source.get('platform'),
            'display_name': source.get('display_name'),
            'base_url': source.get('base_url'),
            'source_config_path': config_path,
            'license_status': source.get('license_status'),
            'permission_status': 'not_requested',
            'priority': source.get('priority', ''),
            'notes': source.get('notes', '')
        })
        summary_path = Path(cache_dir) / source_id / 'source_summary.json'
        pages_path = Path(cache_dir) / source_id / 'pages.jsonl'
        if not summary_path.exists():
            continue
        summary = load_json(summary_path)
        broad_locations = summary.get('broad_location', []) or [source.get('display_name', '')]
        for idx, broad in enumerate(broad_locations or ['']):
            resolved = resolve_broad_location(broad)
            records['signals'].append({
                'signal_id': stable_id(source_id, broad or 'unknown', idx, 'signal'),
                'source_id': source_id,
                'signal_type': 'site_declared_region',
                'raw_text': broad or source.get('display_name'),
                'normalized_label': resolved['location_label'] or broad or source.get('display_name'),
                'latitude': resolved['latitude'],
                'longitude': resolved['longitude'],
                'coordinate_precision': resolved['coordinate_precision'] if resolved['coordinate_precision'] != 'unknown' else 'site_declared_region',
                'species_terms': summary.get('species_seen', []),
                'admin_country': resolved['admin_country'],
                'admin_state': resolved['admin_state'],
                'admin_county': resolved['admin_county'],
                'admin_place': resolved['admin_place'],
                'page_url': source.get('base_url'),
                'post_url': source.get('base_url'),
                'creator_id': source_id,
                'confidence_score': resolved.get('confidence_score', 0.0)
            })
        lead_resolved = resolve_broad_location((broad_locations or [''])[0])
        lead = {
            'lead_id': stable_id(source_id, 'lead'),
            'source_id': source_id,
            'platform': source.get('platform'),
            'creator_handle': source.get('display_name'),
            'creator_display_name': source.get('display_name'),
            'profile_url': source.get('base_url'),
            'contact_path': source.get('contact_path') or source.get('base_url'),
            'source_type': 'personal',
            'location_label': ', '.join(broad_locations),
            'admin_state': lead_resolved.get('admin_state', ''),
            'admin_county': lead_resolved.get('admin_county', ''),
            'admin_place': lead_resolved.get('admin_place', ''),
            'species_terms': summary.get('species_seen', []),
            'sample_urls': [source.get('base_url')],
            'post_count_seen': int(summary.get('page_count') or 0),
            'media_count_seen': int(summary.get('media_count') or 0),
            'video_count_seen': int(summary.get('video_count') or 0),
            'license_status': source.get('license_status'),
            'permission_status': 'not_requested',
            'review_status': 'candidate',
            'notes': source.get('notes', '')
        }
        lead['lead_score'] = score_creator_lead(lead)
        lead['priority'] = priority_for_lead_score(lead['lead_score'])
        records['leads'].append(lead)
        if pages_path.exists():
            for page in read_jsonl(pages_path):
                records['observations'].append({
                    'observation_id': stable_id(source_id, page.get('page_url')),
                    'deployment_id': None,
                    'source_id': source_id,
                    'media_url': '|'.join(page.get('image_urls', [])[:10]),
                    'page_url': page.get('page_url'),
                    'observed_at': page.get('page_date') or '',
                    'species_terms': page.get('species_terms', []),
                    'category_terms': [],
                    'has_person': 0,
                    'has_vehicle': 0,
                    'has_bbox': 0,
                    'sequence_id': '',
                    'license_status': page.get('license_status', source.get('license_status')),
                    'permission_status': 'not_requested'
                })
    return records

def normalize_social_locations(cache_dir, config_path):
    '''
    Normalize location-aware records from social-source cache.
    '''
    records = {'sources': [], 'deployments': [], 'observations': [], 'signals': [], 'leads': []}
    config = load_json(config_path)
    for source in config.get('sources', []):
        source_id = source.get('source_id')
        records['sources'].append({
            'source_id': source_id,
            'source_type': 'social',
            'platform': source.get('platform'),
            'display_name': source.get('display_name'),
            'base_url': source.get('base_url'),
            'source_config_path': config_path,
            'license_status': source.get('license_status'),
            'permission_status': source.get('permission_status'),
            'priority': source.get('priority', ''),
            'notes': source.get('notes', '')
        })
        posts_path = Path(cache_dir) / source_id / 'posts.jsonl'
        if not posts_path.exists():
            continue
        posts = read_jsonl(posts_path)
        by_key = {}
        lead_buckets = {}
        for post in posts:
            broad = (post.get('broad_location') or ['unknown'])[0] if isinstance(post.get('broad_location'), list) else post.get('broad_location') or 'unknown'
            key = broad or post.get('creator_display_name') or post.get('query') or 'unknown'
            bucket = by_key.setdefault(key, {'posts': [], 'species': set()})
            bucket['posts'].append(post)
            bucket['species'].update(post.get('species_terms', []))
            creator_key = post.get('creator_handle') or post.get('creator_display_name') or post.get('creator_id') or post.get('query') or 'unknown'
            lead_bucket = lead_buckets.setdefault(creator_key, {'posts': [], 'species': set(), 'locations': set()})
            lead_bucket['posts'].append(post)
            lead_bucket['species'].update(post.get('species_terms', []))
            if broad:
                lead_bucket['locations'].add(broad)
        for key, bucket in by_key.items():
            resolved = resolve_broad_location(key)
            precision = resolved['coordinate_precision']
            if precision == 'state_centroid':
                precision = 'creator_profile_region'
            elif precision == 'county_centroid':
                precision = 'hashtag_region'
            records['signals'].append({
                'signal_id': stable_id(source_id, key, 'signal'),
                'source_id': source_id,
                'signal_type': 'social_location',
                'raw_text': key,
                'normalized_label': resolved['location_label'] or key,
                'latitude': resolved['latitude'],
                'longitude': resolved['longitude'],
                'coordinate_precision': precision,
                'species_terms': sorted(bucket['species']),
                'admin_country': resolved['admin_country'],
                'admin_state': resolved['admin_state'],
                'admin_county': resolved['admin_county'],
                'admin_place': resolved['admin_place'],
                'page_url': source.get('base_url'),
                'post_url': bucket['posts'][0].get('post_url') or source.get('base_url'),
                'creator_id': bucket['posts'][0].get('creator_id') or '',
                'confidence_score': resolved.get('confidence_score', 0.0)
            })
            for post in bucket['posts']:
                records['observations'].append({
                    'observation_id': stable_id(source_id, post.get('post_url'), post.get('query')),
                    'deployment_id': None,
                    'source_id': source_id,
                    'media_url': '',
                    'page_url': post.get('post_url') or '',
                    'observed_at': post.get('posted_at') or '',
                    'species_terms': post.get('species_terms', []),
                    'category_terms': [],
                    'has_person': 1 if 'person' in post.get('species_terms', []) else 0,
                    'has_vehicle': 1 if 'vehicle' in post.get('species_terms', []) else 0,
                    'has_bbox': 0,
                    'sequence_id': '',
                    'license_status': source.get('license_status'),
                    'permission_status': source.get('permission_status')
                })
        for creator_key, bucket in lead_buckets.items():
            sample = bucket['posts'][0]
            resolved = resolve_broad_location(next(iter(bucket['locations']), sample.get('query') or ''))
            lead = {
                'lead_id': stable_id(source_id, creator_key, 'lead'),
                'source_id': source_id,
                'platform': source.get('platform'),
                'creator_handle': sample.get('creator_handle') or creator_key,
                'creator_display_name': sample.get('creator_display_name') or creator_key,
                'profile_url': sample.get('profile_url') or sample.get('creator_url') or source.get('base_url'),
                'contact_path': sample.get('profile_url') or sample.get('creator_url') or '',
                'source_type': 'social',
                'location_label': ', '.join(sorted(bucket['locations'])) or sample.get('query') or '',
                'admin_state': resolved.get('admin_state', ''),
                'admin_county': resolved.get('admin_county', ''),
                'admin_place': resolved.get('admin_place', ''),
                'species_terms': sorted(bucket['species']),
                'sample_urls': [post.get('post_url') for post in bucket['posts'][:5] if post.get('post_url')],
                'post_count_seen': len(bucket['posts']),
                'media_count_seen': len(bucket['posts']),
                'video_count_seen': len([post for post in bucket['posts'] if post.get('media_type') == 'video']),
                'license_status': source.get('license_status'),
                'permission_status': source.get('permission_status'),
                'review_status': 'candidate',
                'notes': source.get('notes', '')
            }
            lead['lead_score'] = score_creator_lead(lead)
            lead['priority'] = priority_for_lead_score(lead['lead_score'])
            records['leads'].append(lead)
    return records

def upsert_location_records(conn, records):
    '''
    Upsert normalized source/deployment/observation records.
    '''
    cur = conn.cursor()
    now = utc_today()
    for source in records.get('sources', []):
        cur.execute("""
        INSERT OR REPLACE INTO camera_sources
        (source_id, source_type, platform, display_name, base_url, source_config_path, license_status, permission_status, priority, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM camera_sources WHERE source_id = ?), ?), ?)
        """, (
            source.get('source_id'), source.get('source_type'), source.get('platform'), source.get('display_name'),
            source.get('base_url'), source.get('source_config_path'), source.get('license_status'),
            source.get('permission_status'), source.get('priority'), source.get('notes'),
            source.get('source_id'), now, now
        ))
    for deployment in records.get('deployments', []):
        cur.execute("""
        INSERT OR REPLACE INTO camera_deployments
        (deployment_id, source_id, dataset_id, location_id, latitude, longitude, coordinate_precision, coordinate_public, coordinate_obfuscated,
         start_date, end_date, camera_days, image_count, sequence_count, video_count, species_count, species_terms, habitat_terms,
         location_label, admin_country, admin_state, admin_county, admin_place, source_url, page_url, license_status, permission_status,
         confidence_score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM camera_deployments WHERE deployment_id = ?), ?), ?)
        """, (
            deployment.get('deployment_id'), deployment.get('source_id'), deployment.get('dataset_id'), deployment.get('location_id'),
            deployment.get('latitude'), deployment.get('longitude'), deployment.get('coordinate_precision'),
            int(bool(deployment.get('coordinate_public'))), int(bool(deployment.get('coordinate_obfuscated'))),
            deployment.get('start_date'), deployment.get('end_date'), deployment.get('camera_days'),
            int(deployment.get('image_count') or 0), int(deployment.get('sequence_count') or 0), int(deployment.get('video_count') or 0),
            int(deployment.get('species_count') or 0), json.dumps(deployment.get('species_terms', [])), json.dumps(deployment.get('habitat_terms', [])),
            deployment.get('location_label'), deployment.get('admin_country'), deployment.get('admin_state'), deployment.get('admin_county'),
            deployment.get('admin_place'), deployment.get('source_url'), deployment.get('page_url'),
            deployment.get('license_status'), deployment.get('permission_status'), deployment.get('confidence_score'),
            deployment.get('deployment_id'), now, now
        ))
    for observation in records.get('observations', []):
        cur.execute("""
        INSERT OR REPLACE INTO camera_observations
        (observation_id, deployment_id, source_id, media_url, page_url, observed_at, species_terms, category_terms, has_person, has_vehicle, has_bbox, sequence_id,
         license_status, permission_status, confidence_score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM camera_observations WHERE observation_id = ?), ?), ?)
        """, (
            observation.get('observation_id'), observation.get('deployment_id'), observation.get('source_id'), observation.get('media_url'),
            observation.get('page_url'), observation.get('observed_at'), json.dumps(observation.get('species_terms', [])),
            json.dumps(observation.get('category_terms', [])), int(bool(observation.get('has_person'))),
            int(bool(observation.get('has_vehicle'))), int(bool(observation.get('has_bbox'))), observation.get('sequence_id'),
            observation.get('license_status'), observation.get('permission_status'),
            float(observation.get('confidence_score') or 0.0), observation.get('observation_id'), now, now
        ))
    for signal in records.get('signals', []):
        cur.execute("""
        INSERT OR REPLACE INTO location_signals
        (signal_id, source_id, signal_type, raw_text, normalized_label, latitude, longitude, coordinate_precision,
         admin_country, admin_state, admin_county, admin_place, page_url, post_url, creator_id, species_terms,
         confidence_score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM location_signals WHERE signal_id = ?), ?), ?)
        """, (
            signal.get('signal_id'), signal.get('source_id'), signal.get('signal_type'), signal.get('raw_text'),
            signal.get('normalized_label'), signal.get('latitude'), signal.get('longitude'), signal.get('coordinate_precision'),
            signal.get('admin_country'), signal.get('admin_state'), signal.get('admin_county'), signal.get('admin_place'),
            signal.get('page_url'), signal.get('post_url'), signal.get('creator_id'),
            json.dumps(signal.get('species_terms', [])), float(signal.get('confidence_score') or 0.0),
            signal.get('signal_id'), now, now
        ))
    for lead in records.get('leads', []):
        cur.execute("""
        INSERT OR REPLACE INTO creator_leads
        (lead_id, source_id, platform, creator_handle, creator_display_name, profile_url, contact_path, source_type,
         location_label, admin_state, admin_county, admin_place, species_terms, sample_urls, post_count_seen, media_count_seen,
         video_count_seen, license_status, permission_status, review_status, lead_score, priority, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM creator_leads WHERE lead_id = ?), ?), ?)
        """, (
            lead.get('lead_id'), lead.get('source_id'), lead.get('platform'), lead.get('creator_handle'),
            lead.get('creator_display_name'), lead.get('profile_url'), lead.get('contact_path'), lead.get('source_type'),
            lead.get('location_label'), lead.get('admin_state'), lead.get('admin_county'), lead.get('admin_place'),
            json.dumps(lead.get('species_terms', [])), json.dumps(lead.get('sample_urls', [])),
            int(lead.get('post_count_seen') or 0), int(lead.get('media_count_seen') or 0), int(lead.get('video_count_seen') or 0),
            lead.get('license_status'), lead.get('permission_status'), lead.get('review_status', 'candidate'),
            float(lead.get('lead_score') or 0.0), lead.get('priority'), lead.get('notes'),
            lead.get('lead_id'), now, now
        ))
    conn.commit()

def rebuild_coverage_cells(conn, resolution):
    '''
    Recompute fallback grid coverage cells.
    '''
    cur = conn.cursor()
    cur.execute("DELETE FROM coverage_cells")
    deployment_rows = cur.execute("""
    SELECT deployment_id, source_id, latitude, longitude, coordinate_precision, camera_days, image_count, sequence_count, video_count,
           species_terms, confidence_score, start_date, end_date
    FROM camera_deployments
    """).fetchall()
    signal_rows = cur.execute("""
    SELECT signal_id, source_id, latitude, longitude, coordinate_precision, species_terms, confidence_score
    FROM location_signals
    """).fetchall()
    lead_rows = cur.execute("""
    SELECT lead_id, source_id, location_label, admin_state, admin_county, admin_place, species_terms, lead_score
    FROM creator_leads
    """).fetchall()
    buckets = {}
    for row in deployment_rows:
        dep_id, source_id, lat, lon, precision, camera_days, image_count, sequence_count, video_count, species_terms, confidence, start_date, end_date = row
        if lat is None or lon is None:
            continue
        lat_bucket = round(lat, 2)
        lon_bucket = round(lon, 2)
        cell_id = f'grid_{lat_bucket}_{lon_bucket}'
        bucket = buckets.setdefault(cell_id, {
            'sources': set(), 'deployments': 0, 'signals': 0, 'leads': 0, 'camera_days': 0.0, 'image_count': 0,
            'sequence_count': 0, 'video_count': 0, 'species_terms': set(), 'precision_rollup': {},
            'first_seen': '', 'last_seen': '', 'confidence_total': 0.0
        })
        bucket['sources'].add(source_id)
        bucket['deployments'] += 1
        bucket['camera_days'] += float(camera_days or 0.0)
        bucket['image_count'] += int(image_count or 0)
        bucket['sequence_count'] += int(sequence_count or 0)
        bucket['video_count'] += int(video_count or 0)
        bucket['species_terms'].update(safe_json_loads(species_terms, []))
        bucket['precision_rollup'][precision] = bucket['precision_rollup'].get(precision, 0) + 1
        bucket['first_seen'] = min(filter(None, [bucket['first_seen'], start_date])) if bucket['first_seen'] and start_date else (bucket['first_seen'] or start_date or '')
        bucket['last_seen'] = max(filter(None, [bucket['last_seen'], end_date])) if bucket['last_seen'] and end_date else (bucket['last_seen'] or end_date or '')
        bucket['confidence_total'] += float(confidence or 0.0)
    for _, source_id, lat, lon, precision, species_terms, confidence in signal_rows:
        if lat is None or lon is None:
            continue
        cell_id = f'grid_{round(lat, 2)}_{round(lon, 2)}'
        bucket = buckets.setdefault(cell_id, {
            'sources': set(), 'deployments': 0, 'signals': 0, 'leads': 0, 'camera_days': 0.0, 'image_count': 0,
            'sequence_count': 0, 'video_count': 0, 'species_terms': set(), 'precision_rollup': {},
            'first_seen': '', 'last_seen': '', 'confidence_total': 0.0
        })
        bucket['sources'].add(source_id)
        bucket['signals'] += 1
        bucket['species_terms'].update(safe_json_loads(species_terms, []))
        bucket['precision_rollup'][precision] = bucket['precision_rollup'].get(precision, 0) + 1
        bucket['confidence_total'] += float(confidence or 0.0)
    for _, source_id, location_label, state, county, place, species_terms, confidence in lead_rows:
        resolved = resolve_broad_location(location_label or ', '.join(part for part in (place, county, state) if part))
        if resolved.get('latitude') is None or resolved.get('longitude') is None:
            continue
        cell_id = f'grid_{round(resolved["latitude"], 2)}_{round(resolved["longitude"], 2)}'
        bucket = buckets.setdefault(cell_id, {
            'sources': set(), 'deployments': 0, 'signals': 0, 'leads': 0, 'camera_days': 0.0, 'image_count': 0,
            'sequence_count': 0, 'video_count': 0, 'species_terms': set(), 'precision_rollup': {},
            'first_seen': '', 'last_seen': '', 'confidence_total': 0.0
        })
        bucket['sources'].add(source_id)
        bucket['leads'] += 1
        bucket['species_terms'].update(safe_json_loads(species_terms, []))
        precision = resolved.get('coordinate_precision', 'unknown')
        bucket['precision_rollup'][precision] = bucket['precision_rollup'].get(precision, 0) + 1
        bucket['confidence_total'] += float(confidence or 0.0)
    observation_counts = dict(cur.execute("SELECT deployment_id, COUNT(*) FROM camera_observations GROUP BY deployment_id").fetchall())
    for cell_id, bucket in buckets.items():
        dep_ids = [row[0] for row in deployment_rows if row[2] is not None and row[3] is not None and f'grid_{round(row[2],2)}_{round(row[3],2)}' == cell_id]
        observation_count = sum(observation_counts.get(dep_id, 0) for dep_id in dep_ids)
        cur.execute("""
        INSERT OR REPLACE INTO coverage_cells
        (cell_id, grid_type, resolution, source_count, deployment_count, signal_count, observation_count, lead_count, camera_days, image_count,
         sequence_count, video_count, species_terms, precision_rollup, first_seen, last_seen, confidence_score, created_at, updated_at)
        VALUES (?, 'latlon_grid', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cell_id, resolution, len(bucket['sources']), bucket['deployments'], bucket['signals'], observation_count, bucket['leads'], bucket['camera_days'],
            bucket['image_count'], bucket['sequence_count'], bucket['video_count'], json.dumps(sorted(bucket['species_terms'])),
            json.dumps(bucket['precision_rollup'], sort_keys=True), bucket['first_seen'], bucket['last_seen'],
            bucket['confidence_total'] / max(bucket['deployments'] + bucket['signals'] + bucket['leads'], 1), utc_today(), utc_today()
        ))
    conn.commit()

def build_location_index(db_path, include_open_dbs=True, include_personal=True, include_social=True,
                         db_config_path=DEFAULT_DB_CONFIG, personal_config_path=DEFAULT_PERSONAL_SOURCES_CONFIG,
                         social_config_path=DEFAULT_SOCIAL_SOURCES_CONFIG):
    '''
    Build or update the unified location index.
    '''
    init_location_index(db_path)
    conn = sqlite3.connect(db_path)
    if include_open_dbs and os.path.exists(db_config_path):
        upsert_location_records(conn, normalize_open_db_locations(db_config_path, DEFAULT_CACHE_DIR))
    if include_personal and os.path.exists(personal_config_path):
        upsert_location_records(conn, normalize_personal_locations(DEFAULT_PERSONAL_CACHE_DIR, personal_config_path))
    if include_social and os.path.exists(social_config_path):
        upsert_location_records(conn, normalize_social_locations(DEFAULT_SOCIAL_CACHE_DIR, social_config_path))
    rebuild_coverage_cells(conn, 2)
    conn.close()

def import_manual_location_leads(db_path, csv_path):
    '''
    Import manual creator leads and broad location signals from CSV.
    '''
    init_location_index(db_path)
    conn = sqlite3.connect(db_path)
    records = {'sources': [], 'deployments': [], 'observations': [], 'signals': [], 'leads': []}
    source_id = 'manual_leads'
    records['sources'].append({
        'source_id': source_id,
        'source_type': 'manual',
        'platform': 'manual',
        'display_name': 'Manual Leads',
        'base_url': '',
        'source_config_path': csv_path,
        'license_status': 'unknown_contact_required',
        'permission_status': 'not_requested',
        'priority': 'medium',
        'notes': 'Imported from manual CSV'
    })
    with open(csv_path, newline='', encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            location_text = row.get('location_text', '')
            resolved = resolve_broad_location(location_text)
            species_terms = [term.strip() for term in (row.get('species_terms', '') or '').split(',') if term.strip()]
            lead = {
                'lead_id': stable_id(source_id, row.get('name'), row.get('url')),
                'source_id': source_id,
                'platform': row.get('platform', 'manual'),
                'creator_handle': row.get('name', ''),
                'creator_display_name': row.get('name', ''),
                'profile_url': row.get('url', ''),
                'contact_path': row.get('contact_path', ''),
                'source_type': row.get('type', 'manual'),
                'location_label': location_text,
                'admin_state': resolved.get('admin_state', ''),
                'admin_county': resolved.get('admin_county', ''),
                'admin_place': resolved.get('admin_place', ''),
                'species_terms': species_terms,
                'sample_urls': [row.get('url', '')] if row.get('url') else [],
                'post_count_seen': 1,
                'media_count_seen': 1,
                'video_count_seen': 0,
                'license_status': 'unknown_contact_required',
                'permission_status': 'not_requested',
                'review_status': 'candidate',
                'notes': row.get('notes', '')
            }
            lead['lead_score'] = score_creator_lead(lead)
            lead['priority'] = priority_for_lead_score(lead['lead_score'])
            records['leads'].append(lead)
            records['signals'].append({
                'signal_id': stable_id(source_id, row.get('name'), location_text, 'signal'),
                'source_id': source_id,
                'signal_type': 'manual_location',
                'raw_text': location_text,
                'normalized_label': resolved.get('location_label', location_text),
                'latitude': resolved.get('latitude'),
                'longitude': resolved.get('longitude'),
                'coordinate_precision': resolved.get('coordinate_precision', 'unknown'),
                'admin_country': resolved.get('admin_country', ''),
                'admin_state': resolved.get('admin_state', ''),
                'admin_county': resolved.get('admin_county', ''),
                'admin_place': resolved.get('admin_place', ''),
                'page_url': row.get('url', ''),
                'post_url': row.get('url', ''),
                'creator_id': lead['lead_id'],
                'species_terms': species_terms,
                'confidence_score': resolved.get('confidence_score', 0.0)
            })
    upsert_location_records(conn, records)
    rebuild_coverage_cells(conn, 2)
    conn.close()

def export_leads_csv(db_path, output_path, place=None, radius_miles=None, review_status=None):
    '''
    Export creator leads from the location index.
    '''
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
    SELECT lead_id, source_id, platform, creator_handle, creator_display_name, profile_url, contact_path, source_type,
           location_label, admin_state, admin_county, admin_place, species_terms, sample_urls, post_count_seen, media_count_seen,
           video_count_seen, license_status, permission_status, review_status, lead_score, priority, notes
    FROM creator_leads
    ORDER BY lead_score DESC, creator_display_name
    """).fetchall()
    resolved = resolve_broad_location(place) if place else None
    exported = []
    for row in rows:
        if review_status and row[19] != review_status:
            continue
        if resolved and radius_miles is not None:
            lead_resolved = resolve_broad_location(row[8] or ', '.join(part for part in (row[11], row[10], row[9]) if part))
            if lead_resolved.get('latitude') is None or haversine_miles(resolved['latitude'], resolved['longitude'], lead_resolved['latitude'], lead_resolved['longitude']) > radius_miles:
                continue
        exported.append({
            'lead_id': row[0], 'source_id': row[1], 'platform': row[2], 'creator_handle': row[3], 'creator_display_name': row[4],
            'profile_url': row[5], 'contact_path': row[6], 'source_type': row[7], 'location_label': row[8], 'admin_state': row[9],
            'admin_county': row[10], 'admin_place': row[11], 'species_terms': '|'.join(safe_json_loads(row[12], [])),
            'sample_urls': '|'.join(safe_json_loads(row[13], [])), 'post_count_seen': row[14], 'media_count_seen': row[15],
            'video_count_seen': row[16], 'license_status': row[17], 'permission_status': row[18], 'review_status': row[19],
            'lead_score': row[20], 'priority': row[21], 'notes': row[22]
        })
    write_csv(output_path, ['lead_id', 'source_id', 'platform', 'creator_handle', 'creator_display_name', 'profile_url', 'contact_path', 'source_type', 'location_label', 'admin_state', 'admin_county', 'admin_place', 'species_terms', 'sample_urls', 'post_count_seen', 'media_count_seen', 'video_count_seen', 'license_status', 'permission_status', 'review_status', 'lead_score', 'priority', 'notes'], exported)
    conn.close()

def set_creator_lead_status(db_path, lead_id, status):
    '''
    Update lead review status.
    '''
    allowed = {'candidate', 'reviewed', 'not_relevant', 'contacted', 'permission_denied', 'permission_obtained', 'ingested'}
    if status not in allowed:
        raise ConfigError(f'Invalid lead status: {status}')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE creator_leads SET review_status = ?, updated_at = ? WHERE lead_id = ?", (status, utc_today(), lead_id))
    if cur.rowcount == 0:
        conn.close()
        raise ConfigError(f'Lead not found: {lead_id}')
    conn.commit()
    conn.close()

def serve_local_map(port, host='127.0.0.1'):
    '''
    Serve the local map UI and repository data over a simple HTTP server.
    '''
    root_dir = Path(__file__).resolve().parent

    class MapHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root_dir), **kwargs)

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self.path = '/web/index.html'
            return super().do_GET()

        def end_headers(self):
            self.send_header('Cache-Control', 'no-store, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            return super().end_headers()

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer((host, port), MapHandler) as httpd:
        log.info(f'Local map available at http://{host}:{port}/')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            log.info('Stopping local map server.')

def export_locations_geojson(db_path, output_path):
    '''
    Export privacy-safe deployment points to GeoJSON.
    '''
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    deployment_rows = cur.execute("""
    SELECT d.deployment_id, d.source_id, s.source_type, s.platform, s.display_name, d.latitude, d.longitude, d.coordinate_precision,
           d.coordinate_public, d.coordinate_obfuscated, d.image_count, d.sequence_count, d.video_count, d.camera_days, d.species_terms,
           d.admin_state, d.admin_county, d.location_label, d.license_status, d.permission_status, d.confidence_score
    FROM camera_deployments d JOIN camera_sources s ON d.source_id = s.source_id
    """).fetchall()
    signal_rows = cur.execute("""
    SELECT g.signal_id, g.source_id, s.source_type, s.platform, s.display_name, g.latitude, g.longitude, g.coordinate_precision,
           g.signal_type, g.raw_text, g.normalized_label, g.species_terms, g.admin_state, g.admin_county, g.admin_place, g.confidence_score
    FROM location_signals g JOIN camera_sources s ON g.source_id = s.source_id
    """).fetchall()
    features = []
    for row in deployment_rows:
        if row[5] is None or row[6] is None:
            continue
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [row[6], row[5]]},
            'properties': {
                'record_type': 'deployment',
                'deployment_id': row[0], 'source_id': row[1], 'source_type': row[2], 'platform': row[3], 'display_name': row[4],
                'coordinate_precision': row[7], 'coordinate_public': bool(row[8]), 'coordinate_obfuscated': bool(row[9]),
                'image_count': row[10], 'sequence_count': row[11], 'video_count': row[12], 'camera_days': row[13],
                'species_terms': safe_json_loads(row[14], []), 'admin_state': row[15], 'admin_county': row[16],
                'location_label': row[17], 'license_status': row[18], 'permission_status': row[19], 'confidence_score': row[20]
            }
        })
    for row in signal_rows:
        if row[5] is None or row[6] is None:
            continue
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [row[6], row[5]]},
            'properties': {
                'record_type': 'signal',
                'signal_id': row[0], 'source_id': row[1], 'source_type': row[2], 'platform': row[3], 'display_name': row[4],
                'coordinate_precision': row[7], 'signal_type': row[8], 'raw_text': row[9], 'location_label': row[10],
                'species_terms': safe_json_loads(row[11], []), 'admin_state': row[12], 'admin_county': row[13],
                'admin_place': row[14], 'confidence_score': row[15]
            }
        })
    write_json(output_path, {'type': 'FeatureCollection', 'features': features})
    conn.close()

def grid_polygon(lat, lon, step=0.01):
    '''
    Build a fallback square polygon around a grid centroid.
    '''
    return [[
        [lon - step, lat - step], [lon + step, lat - step], [lon + step, lat + step],
        [lon - step, lat + step], [lon - step, lat - step]
    ]]

def export_h3_coverage_geojson(db_path, output_path, resolution):
    '''
    Export fallback grid coverage GeoJSON.
    '''
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
    SELECT cell_id, grid_type, resolution, source_count, deployment_count, signal_count, observation_count, lead_count, camera_days, image_count,
           sequence_count, video_count, species_terms, precision_rollup, first_seen, last_seen, confidence_score
    FROM coverage_cells
    """).fetchall()
    features = []
    for row in rows:
        cell_id = row[0]
        parts = cell_id.split('_')
        lat = float(parts[1])
        lon = float(parts[2])
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': grid_polygon(lat, lon)},
            'properties': {
                'cell_id': row[0], 'grid_type': row[1], 'resolution': row[2], 'source_count': row[3],
                'deployment_count': row[4], 'signal_count': row[5], 'observation_count': row[6], 'lead_count': row[7],
                'camera_days': row[8], 'image_count': row[9], 'sequence_count': row[10], 'video_count': row[11],
                'species_terms': safe_json_loads(row[12], []), 'precision_rollup': safe_json_loads(row[13], {}),
                'first_seen': row[14], 'last_seen': row[15], 'confidence_score': row[16]
            }
        })
    write_json(output_path, {'type': 'FeatureCollection', 'features': features})
    conn.close()

def export_admin_rollups(db_path, county_path, state_path):
    '''
    Export county and state rollups.
    '''
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    county_rows = []
    county_stats = {}
    for row in cur.execute("""
    SELECT admin_state, admin_county, COUNT(*), SUM(image_count), SUM(video_count), AVG(confidence_score), GROUP_CONCAT(species_terms, '|'), GROUP_CONCAT(DISTINCT coordinate_precision)
    FROM camera_deployments WHERE admin_county != '' GROUP BY admin_state, admin_county
    """):
        county_stats[(row[0], row[1])] = {
            'state': row[0], 'county': row[1], 'deployment_count': row[2], 'signal_count': 0, 'observation_count': 0, 'lead_count': 0,
            'image_count': row[3] or 0, 'video_count': row[4] or 0, 'species_terms': '|'.join(merge_species_blobs(row[6])),
            'precision_rollup': row[7] or '', 'confidence_avg': round(row[5] or 0.0, 3)
        }
    for row in cur.execute("SELECT admin_state, admin_county, COUNT(*) FROM location_signals WHERE admin_county != '' GROUP BY admin_state, admin_county"):
        bucket = county_stats.setdefault((row[0], row[1]), {
            'state': row[0], 'county': row[1], 'deployment_count': 0, 'signal_count': 0, 'observation_count': 0, 'lead_count': 0,
            'image_count': 0, 'video_count': 0, 'species_terms': '', 'precision_rollup': '', 'confidence_avg': 0.0
        })
        bucket['signal_count'] += row[2]
    for row in cur.execute("""
    SELECT d.admin_state, d.admin_county, COUNT(o.observation_id)
    FROM camera_deployments d JOIN camera_observations o ON d.deployment_id = o.deployment_id
    WHERE d.admin_county != '' GROUP BY d.admin_state, d.admin_county
    """):
        bucket = county_stats.setdefault((row[0], row[1]), {
            'state': row[0], 'county': row[1], 'deployment_count': 0, 'signal_count': 0, 'observation_count': 0, 'lead_count': 0,
            'image_count': 0, 'video_count': 0, 'species_terms': '', 'precision_rollup': '', 'confidence_avg': 0.0
        })
        bucket['observation_count'] += row[2]
    for row in cur.execute("SELECT admin_state, admin_county, COUNT(*) FROM creator_leads WHERE admin_county != '' GROUP BY admin_state, admin_county"):
        bucket = county_stats.setdefault((row[0], row[1]), {
            'state': row[0], 'county': row[1], 'deployment_count': 0, 'signal_count': 0, 'observation_count': 0, 'lead_count': 0,
            'image_count': 0, 'video_count': 0, 'species_terms': '', 'precision_rollup': '', 'confidence_avg': 0.0
        })
        bucket['lead_count'] += row[2]
    for bucket in county_stats.values():
        county_rows.append({
            'state': bucket['state'], 'county': bucket['county'], 'deployment_count': bucket['deployment_count'], 'signal_count': bucket['signal_count'],
            'observation_count': bucket['observation_count'], 'lead_count': bucket['lead_count'], 'image_count': bucket['image_count'],
            'video_count': bucket['video_count'], 'species_terms': bucket['species_terms'], 'precision_rollup': bucket['precision_rollup'],
            'confidence_avg': bucket['confidence_avg']
        })
    state_rows = []
    state_stats = {}
    for row in county_rows:
        bucket = state_stats.setdefault(row['state'], {
            'state': row['state'], 'deployment_count': 0, 'signal_count': 0, 'observation_count': 0, 'lead_count': 0,
            'image_count': 0, 'video_count': 0, 'species_terms': set(), 'precision_rollup': set(), 'confidence_total': 0.0, 'confidence_count': 0
        })
        bucket['deployment_count'] += row['deployment_count']
        bucket['signal_count'] += row['signal_count']
        bucket['observation_count'] += row['observation_count']
        bucket['lead_count'] += row['lead_count']
        bucket['image_count'] += row['image_count']
        bucket['video_count'] += row['video_count']
        bucket['species_terms'].update(filter(None, row['species_terms'].split('|')))
        bucket['precision_rollup'].update(filter(None, row['precision_rollup'].split(',')))
        bucket['confidence_total'] += row['confidence_avg']
        bucket['confidence_count'] += 1
    for bucket in state_stats.values():
        state_rows.append({
            'state': bucket['state'], 'deployment_count': bucket['deployment_count'], 'signal_count': bucket['signal_count'],
            'observation_count': bucket['observation_count'], 'lead_count': bucket['lead_count'], 'image_count': bucket['image_count'],
            'video_count': bucket['video_count'], 'species_terms': '|'.join(sorted(bucket['species_terms'])),
            'precision_rollup': ','.join(sorted(bucket['precision_rollup'])),
            'confidence_avg': round(bucket['confidence_total'] / max(bucket['confidence_count'], 1), 3)
        })
    write_csv(county_path, ['state', 'county', 'deployment_count', 'signal_count', 'observation_count', 'lead_count', 'image_count', 'video_count', 'species_terms', 'precision_rollup', 'confidence_avg'], county_rows)
    write_csv(state_path, ['state', 'deployment_count', 'signal_count', 'observation_count', 'lead_count', 'image_count', 'video_count', 'species_terms', 'precision_rollup', 'confidence_avg'], state_rows)
    conn.close()

def generate_coverage_report(db_path, output_path):
    '''
    Generate a global markdown coverage report.
    '''
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    source_rows = cur.execute("""
    SELECT s.source_type, COUNT(DISTINCT s.source_id), COUNT(DISTINCT d.deployment_id), COUNT(DISTINCT g.signal_id),
           COUNT(o.observation_id), COUNT(DISTINCT l.lead_id), SUM(d.image_count), SUM(d.video_count), AVG(d.confidence_score)
    FROM camera_sources s
    LEFT JOIN camera_deployments d ON s.source_id = d.source_id
    LEFT JOIN location_signals g ON s.source_id = g.source_id
    LEFT JOIN camera_observations o ON d.deployment_id = o.deployment_id
    LEFT JOIN creator_leads l ON s.source_id = l.source_id
    GROUP BY s.source_type
    """).fetchall()
    precision_rows = cur.execute("""
    SELECT 'deployment', coordinate_precision, COUNT(*), SUM(image_count) FROM camera_deployments GROUP BY coordinate_precision
    UNION ALL
    SELECT 'signal', coordinate_precision, COUNT(*), 0 FROM location_signals GROUP BY coordinate_precision
    """).fetchall()
    state_rows = cur.execute("SELECT admin_state, COUNT(*), SUM(image_count), AVG(confidence_score) FROM camera_deployments WHERE admin_state != '' GROUP BY admin_state ORDER BY COUNT(*) DESC LIMIT 10").fetchall()
    county_rows = cur.execute("SELECT admin_state, admin_county, COUNT(*), SUM(image_count), AVG(confidence_score) FROM camera_deployments WHERE admin_county != '' GROUP BY admin_state, admin_county ORDER BY COUNT(*) DESC LIMIT 10").fetchall()
    lead_status_rows = cur.execute("SELECT review_status, COUNT(*), AVG(lead_score) FROM creator_leads GROUP BY review_status ORDER BY COUNT(*) DESC").fetchall()
    total_dep = sum(row[2] for row in precision_rows) or 1
    fov = calculate_direct_fov_area()
    lines = [
        '# Trail-Cam Coverage Report', '', '## Summary', '',
        f'Total sources: {cur.execute("SELECT COUNT(*) FROM camera_sources").fetchone()[0]}',
        f'Total deployments: {cur.execute("SELECT COUNT(*) FROM camera_deployments").fetchone()[0]}',
        f'Total location signals: {cur.execute("SELECT COUNT(*) FROM location_signals").fetchone()[0]}',
        f'Total observations: {cur.execute("SELECT COUNT(*) FROM camera_observations").fetchone()[0]}',
        f'Total creator leads: {cur.execute("SELECT COUNT(*) FROM creator_leads").fetchone()[0]}', '',
        '## Source Breakdown', '', 'source_type | sources | deployments | signals | observations | leads | image_count | video_count | confidence_avg',
        '--- | --- | --- | --- | --- | --- | --- | --- | ---'
    ]
    for row in source_rows:
        lines.append(f'{row[0]} | {row[1] or 0} | {row[2] or 0} | {row[3] or 0} | {row[4] or 0} | {row[5] or 0} | {row[6] or 0} | {row[7] or 0} | {round(row[8] or 0.0, 3)}')
    lines.extend(['', '## Location Precision', '', 'record_type | coordinate_precision | count | image_count | percent', '--- | --- | --- | --- | ---'])
    for row in precision_rows:
        lines.append(f'{row[0]} | {row[1]} | {row[2]} | {row[3] or 0} | {round((row[2] / total_dep) * 100, 1)}%')
    lines.extend(['', '## Lead Pipeline', '', 'review_status | lead_count | avg_score', '--- | --- | ---'])
    for row in lead_status_rows:
        lines.append(f'{row[0]} | {row[1]} | {round(row[2] or 0.0, 3)}')
    lines.extend(['', '## Top States', '', 'state | deployments | image_count | confidence_avg', '--- | --- | --- | ---'])
    for row in state_rows:
        lines.append(f'{row[0]} | {row[1]} | {row[2] or 0} | {round(row[3] or 0.0, 3)}')
    lines.extend(['', '## Top Counties', '', 'state | county | deployments | image_count | confidence_avg', '--- | --- | --- | --- | ---'])
    for row in county_rows:
        lines.append(f'{row[0]} | {row[1]} | {row[2]} | {row[3] or 0} | {round(row[4] or 0.0, 3)}')
    lines.extend(['', '## Aggregate FOV Estimate', '', f'Estimated effective area per camera: {round(fov["effective_area_acres"], 3)} acres', '', '## Gaps', '', '- unknown coordinates', '- location_id_only datasets', '- state-only social leads', '- county-only personal leads', '', '## Caveats', '', '- Coverage maps show confirmed deployments, broad location signals, and leads.', '- County/state/social points are not exact trail-camera locations.', '- Exact private locations are never inferred.', '', '## Recommended Acquisition Targets', '', '- Prioritize regions with many broad leads but few exact/fuzzed public deployments.', ''])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text('\n'.join(lines), encoding='utf-8')
    conn.close()

def generate_place_coverage_report(db_path, place, radius_miles, output_path):
    '''
    Generate a place/radius markdown report.
    '''
    resolved = resolve_broad_location(place)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    deployment_rows = cur.execute("SELECT coordinate_precision, latitude, longitude, admin_state, admin_county, admin_place, location_label, species_terms FROM camera_deployments").fetchall()
    signal_rows = cur.execute("SELECT coordinate_precision, latitude, longitude, admin_state, admin_county, admin_place, normalized_label, species_terms FROM location_signals").fetchall()
    lead_rows = cur.execute("SELECT location_label, admin_state, admin_county, admin_place, species_terms FROM creator_leads").fetchall()
    exact = fuzzed = broad = lead_count = 0
    species = set()
    place_tokens = [token.lower() for token in (resolved.get('admin_place', ''), resolved.get('admin_county', ''), resolved.get('admin_state', ''), place) if token]
    for row in deployment_rows:
        precision, lat, lon, state, county, admin_place, label, species_terms = row
        if precision in ('exact_public', 'fuzzed_public', 'project_centroid', 'park_or_public_land_centroid') and lat is not None and lon is not None:
            dist = haversine_miles(resolved['latitude'], resolved['longitude'], lat, lon)
            if dist is not None and dist <= radius_miles:
                if precision == 'exact_public':
                    exact += 1
                else:
                    fuzzed += 1
                species.update(safe_json_loads(species_terms, []))
    for row in signal_rows:
        precision, lat, lon, state, county, admin_place, label, species_terms = row
        in_radius = lat is not None and lon is not None and haversine_miles(resolved['latitude'], resolved['longitude'], lat, lon) <= radius_miles
        text = ' '.join(part for part in (state, county, admin_place, label) if part).lower()
        if in_radius or any(token in text for token in place_tokens):
            broad += 1
            species.update(safe_json_loads(species_terms, []))
    for label, state, county, admin_place, species_terms in lead_rows:
        text = ' '.join(part for part in (label, state, county, admin_place) if part).lower()
        if any(token in text for token in place_tokens):
            lead_count += 1
            species.update(safe_json_loads(species_terms, []))
    confidence = 'Low'
    if exact + fuzzed >= 5:
        confidence = 'High'
    elif broad + lead_count >= 5:
        confidence = 'Medium'
    lines = [
        f'# Trail-Cam Coverage Near {place}', '', f'Radius: {radius_miles} miles', '', '## Summary', '',
        f'Exact public camera deployments within {radius_miles} miles: {exact}',
        f'Fuzzed public deployments within {radius_miles} miles: {fuzzed}',
        f'Broad location signals intersecting radius: {broad}',
        f'Creator leads: {lead_count}',
        f'Coverage confidence: {confidence}', '',
        '## Species/Event Terms', '', ', '.join(sorted(species)) if species else 'None', '',
        '## Interpretation', '', '- Confirmed deployments are separate from broad location signals.', '- Broad signals indicate likely local camera activity but not exact placement.', '- Creator leads are outreach candidates, not confirmed deployments.', ''
    ]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text('\n'.join(lines), encoding='utf-8')
    cur.execute("""
    INSERT OR REPLACE INTO coverage_reports
    (report_id, report_type, place_name, latitude, longitude, radius_miles, report_path, summary_json, created_at)
    VALUES (?, 'place_radius', ?, ?, ?, ?, ?, ?, ?)
    """, (
        stable_id(place, radius_miles), place, resolved['latitude'], resolved['longitude'], radius_miles, output_path,
        json.dumps({'exact': exact, 'fuzzed': fuzzed, 'broad': broad, 'creator_leads': lead_count, 'confidence': confidence}, sort_keys=True),
        utc_today()
    ))
    conn.commit()
    conn.close()

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
    parser.add_argument('--build-location-index', action='store_true', help='Build the unified trail-cam location index.')
    parser.add_argument('--location-index', default=f'{DEFAULT_COVERAGE_DIR}/trailcam_location_index.sqlite', help='Path to the SQLite location index.')
    parser.add_argument('--include-open-dbs', action='store_true', help='Include open/institutional DBs in the location index.')
    parser.add_argument('--include-personal', action='store_true', help='Include personal-source cache in the location index.')
    parser.add_argument('--include-social', action='store_true', help='Include social-source cache in the location index.')
    parser.add_argument('--export-geojson', help='Write location points GeoJSON to this path.')
    parser.add_argument('--export-h3-coverage', help='Write H3/grid coverage GeoJSON to this path.')
    parser.add_argument('--h3-resolution', type=int, default=7, help='Requested H3 resolution; fallback grid uses a fixed lat/lon grid.')
    parser.add_argument('--serve-map', action='store_true', help='Host the local camera map UI.')
    parser.add_argument('--map-port', type=int, default=8765, help='Port for --serve-map.')
    parser.add_argument('--export-admin-rollups', action='store_true', help='Write county/state rollup CSVs.')
    parser.add_argument('--county-rollup', default=f'{DEFAULT_COVERAGE_DIR}/trailcam_county_rollup.csv', help='County rollup CSV path.')
    parser.add_argument('--state-rollup', default=f'{DEFAULT_COVERAGE_DIR}/trailcam_state_rollup.csv', help='State rollup CSV path.')
    parser.add_argument('--coverage-report', help='Write a global coverage markdown report.')
    parser.add_argument('--coverage-place', help='Generate a place/radius coverage report for this place name.')
    parser.add_argument('--radius-miles', type=float, default=25.0, help='Radius in miles for --coverage-place.')
    parser.add_argument('--coverage-place-report', help='Write the place/radius coverage markdown report to this path.')
    parser.add_argument('--lead-status', help='Optional creator lead review-status filter for location exports.')
    parser.add_argument('--lead-id', help='Creator lead id to update.')
    parser.add_argument('--set-lead-status', help='New review status for --lead-id.')
    parser.add_argument('--import-manual-leads', help='Import manual location leads from CSV.')
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
    parser.add_argument('--export-leads', help='Write personal leads or location creator leads to this CSV path.')
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
        args.import_manual_seeds, args.build_location_index, args.export_geojson, args.export_h3_coverage,
        args.serve_map, args.export_admin_rollups, args.coverage_report, args.coverage_place, args.import_manual_leads,
        args.lead_id and args.set_lead_status,
        args.scan_dbs, args.map, args.list_api, args.metadata_extract,
        args.find, args.export_leads, args.export_social_leads
    ]):
        parser.error('Choose one action: --init-creds, --check, --validate-personal-config, --check-personal, --discover, --validate-social-config, --check-social, --discover-social, --import-manual-seeds, --build-location-index, --export-geojson, --export-h3-coverage, --export-admin-rollups, --coverage-report, --coverage-place, --import-manual-leads, --lead-id/--set-lead-status, --scan-dbs, --map, --list-api, --metadata-extract, --export-leads, --export-social-leads, or --find')
    if args.metadata_extract and not args.db:
        parser.error('--metadata-extract requires --db')
    if bool(args.lead_id) != bool(args.set_lead_status):
        parser.error('--lead-id and --set-lead-status must be used together')
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
    location_export_leads = bool(args.export_leads and ('trailcam_coverage' in args.export_leads or any([args.build_location_index, args.export_geojson, args.export_h3_coverage, args.export_admin_rollups, args.coverage_report, args.coverage_place, args.import_manual_leads, args.lead_id, args.set_lead_status, args.lead_status, args.serve_map])))
    location_mode = any([args.build_location_index, args.export_geojson, args.export_h3_coverage, args.serve_map, args.export_admin_rollups, args.coverage_report, args.coverage_place, args.import_manual_leads, args.lead_id and args.set_lead_status, location_export_leads])
    social_mode = any([args.validate_social_config, args.check_social, args.discover_social, args.export_social_leads, args.import_manual_seeds]) or (args.social_source and args.find)
    personal_mode = any([args.validate_personal_config, args.check_personal, args.discover, args.export_leads and not location_export_leads]) or (args.personal_source and args.find)

    if location_mode:
        include_open = args.include_open_dbs
        include_personal = args.include_personal
        include_social = args.include_social
        if args.build_location_index and not any([include_open, include_personal, include_social]):
            include_open = include_personal = include_social = True
        if args.build_location_index:
            build_location_index(args.location_index, include_open, include_personal, include_social)
        if args.export_geojson:
            export_locations_geojson(args.location_index, args.export_geojson)
        if args.export_h3_coverage:
            export_h3_coverage_geojson(args.location_index, args.export_h3_coverage, args.h3_resolution)
        if args.export_admin_rollups:
            export_admin_rollups(args.location_index, args.county_rollup, args.state_rollup)
        if args.coverage_report:
            generate_coverage_report(args.location_index, args.coverage_report)
        if args.coverage_place:
            report_path = args.coverage_place_report or f'{DEFAULT_COVERAGE_DIR}/reports/{safe_filename(args.coverage_place)}_{int(args.radius_miles)}mi.md'
            generate_place_coverage_report(args.location_index, args.coverage_place, args.radius_miles, report_path)
        if args.import_manual_leads:
            import_manual_location_leads(args.location_index, args.import_manual_leads)
        if location_export_leads:
            export_leads_csv(args.location_index, args.export_leads, args.coverage_place, args.radius_miles if args.coverage_place else None, args.lead_status)
        if args.lead_id and args.set_lead_status:
            set_creator_lead_status(args.location_index, args.lead_id, args.set_lead_status)
        if args.serve_map:
            serve_local_map(args.map_port)
        return

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
