import re
from typing import Optional, Dict
from datetime import datetime
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
from schemas import SchedulingIntent
from date_utils import parse_meeting_time

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
_pipe = None

def get_classifier_pipeline():
    """Lazy loader for Qwen2.5-1.5B-Instruct pipeline (cached in memory)."""
    global _pipe
    if _pipe is None:
        print(f"🧠 Loading {MODEL_ID} into memory on CPU...")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, clean_up_tokenization_spaces=False)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float32,
        ).to("cpu")

        _pipe = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
            max_new_tokens=90,
            device="cpu",
            do_sample=False,
        )
        print("✅ Classifier model ready.")
    return _pipe

SYSTEM_PROMPT = """You are an email triage assistant.
Read the email and current state carefully.

Output exactly 4 fields:
SUMMARY: A 1-sentence plain-English summary of what the sender wants.
TIME_PHRASE: The exact quoted proposed meeting date/time from the email, or None.
TOPIC: A 2-4 word meeting subject or topic (e.g. "Partnership Sync", "Intro Call", "Product Demo", or "Billing Dispute").
INTENT: Exactly one of:
- PROPOSE_TIME: Sender asks for or proposes a meeting time, or asks to reschedule.
- CONFIRM_TIME: Sender agrees to or confirms the currently scheduled hold.
- CANCEL_TIME: Sender declines or cancels the upcoming meeting.
- UNRELATED: Email is a general inquiry, complaint, invoice issue, or non-scheduling.
- UNSURE: Conversational side-chatter, praise, or unclear scheduling intent.

Format:
SUMMARY: <1 sentence summary>
TIME_PHRASE: <exact quoted time from email, or None>
TOPIC: <2-4 word topic>
INTENT: <PROPOSE_TIME | CONFIRM_TIME | CANCEL_TIME | UNRELATED | UNSURE>
"""

FEW_SHOT_EXAMPLES = [
    {
        "role": "user",
        "content": """Current Contact Status: new
Active Scheduled Event: None
Email Subject: Intro chat
Email Body:
\"\"\"Hi Micah, are you free for a 30-minute intro call next Monday, Nov 2 at 10:00 AM PST?\"\"\""""
    },
    {
        "role": "assistant",
        "content": """SUMMARY: Sender is requesting a 30-minute intro call on next Monday, Nov 2 at 10:00 AM PST.
TIME_PHRASE: next Monday, Nov 2 at 10:00 AM PST
TOPIC: Intro Call
INTENT: PROPOSE_TIME"""
    },
    {
        "role": "user",
        "content": """Current Contact Status: wait for them
Active Scheduled Event: [HOLD] Sync at 2026-10-15 10:00 PST (Status: hold)
Email Subject: Re: Sync
Email Body:
\"\"\"Thursday at 10:00 AM PST works great for me. See you then!\"\"\""""
    },
    {
        "role": "assistant",
        "content": """SUMMARY: Sender confirmed that Thursday at 10:00 AM PST works for the scheduled sync.
TIME_PHRASE: Thursday at 10:00 AM PST
TOPIC: Partnership Sync
INTENT: CONFIRM_TIME"""
    },
    {
        "role": "user",
        "content": """Current Contact Status: hold for me
Active Scheduled Event: [HOLD] Lunch at 2026-10-16 12:00 PST (Status: hold)
Email Subject: Can't make lunch
Email Body:
\"\"\"So sorry, I have to cancel our sync this week due to an emergency. Will reach back out later.\"\"\""""
    },
    {
        "role": "assistant",
        "content": """SUMMARY: Sender is cancelling the scheduled lunch due to an unexpected emergency.
TIME_PHRASE: None
TOPIC: Coffee Sync
INTENT: CANCEL_TIME"""
    },
    {
        "role": "user",
        "content": """Current Contact Status: new
Active Scheduled Event: None
Email Subject: Invoice problem
Email Body:
\"\"\"Hello, our card was charged twice for invoice #442. Please reverse the charge immediately.\"\"\""""
    },
    {
        "role": "assistant",
        "content": """SUMMARY: Customer is demanding an immediate refund for duplicate charges on invoice #442.
TIME_PHRASE: None
TOPIC: Billing Dispute
INTENT: UNRELATED"""
    },
    {
        "role": "user",
        "content": """Current Contact Status: hold for me
Active Scheduled Event: [HOLD] Sync at 2026-10-22 14:00 PST (Status: hold)
Email Subject: Proposal notes
Email Body:
\"\"\"Yes absolutely, looks great! Thanks for sending over the revision.\"\"\""""
    },
    {
        "role": "assistant",
        "content": """SUMMARY: Sender praised the document revision with no calendar scheduling intent.
TIME_PHRASE: None
TOPIC: Project Proposal
INTENT: UNSURE"""
    }
]

def classify_email(
    subject: str,
    body: str,
    contact_status: str = "new",
    active_event: Optional[Dict] = None,
    email_date: Optional[datetime] = None,
) -> Dict:
    """
    Performs cognitive classification on an email using Qwen2.5-1.5B-Instruct.
    Returns parsed intent, summary, topic, and timezone-aware meeting datetime.
    """
    pipe = get_classifier_pipeline()

    active_event_str = "None"
    if active_event:
        active_event_str = f"{active_event.get('title', 'Event')} at {active_event.get('start_time', '')} (Status: {active_event.get('status', 'hold')})"

    user_content = f"""Current Contact Status: {contact_status}
Active Scheduled Event: {active_event_str}
Email Subject: {subject}
Email Body:
\"\"\"{body}\"\"\""""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(FEW_SHOT_EXAMPLES)
    messages.append({"role": "user", "content": user_content})

    formatted_prompt = pipe.tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    outputs = pipe(formatted_prompt)
    generated_text = outputs[0]["generated_text"][len(formatted_prompt):].strip()

    # Regex extraction
    summary_match = re.search(r"SUMMARY:\s*([^\n]+)", generated_text)
    time_match = re.search(r"TIME_PHRASE:\s*([^\n]+)", generated_text)
    topic_match = re.search(r"TOPIC:\s*([^\n]+)", generated_text)
    intent_match = re.search(r"INTENT:\s*([A-Z_]+)", generated_text)

    summary = summary_match.group(1).strip() if summary_match else ""
    time_phrase = time_match.group(1).strip() if time_match else "None"
    topic = topic_match.group(1).strip() if topic_match else "Meeting"
    raw_intent = intent_match.group(1).strip() if intent_match else ""

    # Clean up topic
    if not topic or topic.lower() == "none":
        topic = "Intro Call"

    # Defensive guard: Empty summary -> model failed -> Safe Default
    if not summary:
        return {
            "intent": SchedulingIntent.AMBIGUOUS,
            "summary": "[Warning]: Empty summary produced; routing to Needs Attention.",
            "topic": topic,
            "time_phrase": "None",
            "parsed_time": None,
            "raw_output": generated_text
        }

    has_time = (time_phrase.lower() != "none" and len(time_phrase.strip()) > 0)
    parsed_dt = parse_meeting_time(time_phrase, anchor_date=email_date) if has_time else None

    # Domain Guardrail Logic
    if raw_intent == "CANCEL_TIME":
        intent = SchedulingIntent.CANCEL_HOLD
    elif raw_intent == "CONFIRM_TIME":
        # Can only confirm if active hold exists AND email references a time or explicit agreement
        if active_event and has_time:
            intent = SchedulingIntent.CONFIRM_HOLD
        else:
            intent = SchedulingIntent.AMBIGUOUS
    elif raw_intent == "PROPOSE_TIME":
        intent = SchedulingIntent.PROPOSE_NEW_TIME if (has_time and parsed_dt) else SchedulingIntent.AMBIGUOUS
    elif raw_intent == "UNRELATED":
        intent = SchedulingIntent.AMBIGUOUS if has_time else SchedulingIntent.NO_CALENDAR_ACTION
    else:
        intent = SchedulingIntent.AMBIGUOUS

    return {
        "intent": intent,
        "summary": summary,
        "topic": topic,
        "time_phrase": time_phrase,
        "parsed_time": parsed_dt,
        "raw_output": generated_text
    }

