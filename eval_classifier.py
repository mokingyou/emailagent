import json
import re
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
from schemas import SchedulingIntent

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
device = "cpu"

print(f"Loading {MODEL_ID} on {device}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, clean_up_tokenization_spaces=False)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.float32,
).to(device)

pipe = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=85,
    device=device,
    do_sample=False,
)

SYSTEM_PROMPT = """You are an email triage assistant.
Read the email and current state carefully.

Output exactly 3 fields:
SUMMARY: A 1-sentence plain-English summary of the sender's message.
TIME_PHRASE: The exact quoted proposed meeting date/time from the email, or None.
INTENT: Exactly one of:
- PROPOSE_TIME: Sender asks for or proposes a meeting time, or asks to reschedule.
- CONFIRM_TIME: Sender agrees to or confirms the currently scheduled hold.
- CANCEL_TIME: Sender declines or cancels the upcoming meeting.
- UNRELATED: Email is a general inquiry, complaint, invoice issue, or non-scheduling.
- UNSURE: Conversational side-chatter, praise, or unclear scheduling intent.

Format:
SUMMARY: <1 sentence summary>
TIME_PHRASE: <exact quoted time from email, or None>
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
INTENT: UNSURE"""
    }
]

def classify_message(fixture: dict) -> dict:
    active_event = fixture.get("active_event")
    active_event_str = "None"
    if active_event:
        active_event_str = f"{active_event['title']} at {active_event['start_time']} (Status: {active_event['status']})"

    target_user_content = f"""Current Contact Status: {fixture.get('current_contact_status', 'new')}
Active Scheduled Event: {active_event_str}
Email Subject: {fixture['subject']}
Email Body:
\"\"\"{fixture['body']}\"\"\""""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(FEW_SHOT_EXAMPLES)
    messages.append({"role": "user", "content": target_user_content})

    formatted_prompt = pipe.tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    outputs = pipe(formatted_prompt)
    generated_text = outputs[0]["generated_text"][len(formatted_prompt):].strip()

    # --- 1. Extraction ---
    summary_match = re.search(r"SUMMARY:\s*([^\n]+)", generated_text)
    time_match = re.search(r"TIME_PHRASE:\s*([^\n]+)", generated_text)
    intent_match = re.search(r"INTENT:\s*([A-Z_]+)", generated_text)

    summary = summary_match.group(1).strip() if summary_match else ""
    time_phrase = time_match.group(1).strip() if time_match else "None"
    raw_intent = intent_match.group(1).strip() if intent_match else ""

    # Null guardrail
    if not summary:
        return {
            "predicted_intent": "AMBIGUOUS",
            "time_phrase": "None",
            "summary": "[Warning]: Empty summary produced; defaulting to Needs Attention.",
            "raw_output": generated_text
        }

    has_time = (time_phrase.lower() != "none" and len(time_phrase.strip()) > 0)

    # --- 2. Clean State Mapping ---
    if raw_intent == "CANCEL_TIME":
        predicted_intent = "CANCEL_HOLD"
    elif raw_intent == "CONFIRM_TIME":
        # Domain Guardrail: To confirm an active hold, the email must explicitly reference/agree to a time!
        # If they say "yes/confirmed" with no time phrase, it's ambiguous (e.g. approving a proposal or doc).
        if active_event and has_time:
            predicted_intent = "CONFIRM_HOLD"
        else:
            predicted_intent = "AMBIGUOUS"
    elif raw_intent == "PROPOSE_TIME":
        predicted_intent = "PROPOSE_NEW_TIME" if has_time else "AMBIGUOUS"
    elif raw_intent == "UNRELATED":
        predicted_intent = "AMBIGUOUS" if has_time else "NO_CALENDAR_ACTION"
    else:
        # UNSURE or anything ambiguous defaults to AMBIGUOUS (Needs Attention)
        predicted_intent = "AMBIGUOUS"

    return {
        "predicted_intent": predicted_intent,
        "time_phrase": time_phrase,
        "summary": summary,
        "raw_output": generated_text
    }

def run_evaluation():
    with open("tests/fixtures/mock_emails.json", "r") as f:
        fixtures = json.load(f)

    print("\n" + "="*80)
    print(f"RUNNING EVALUATION ON {MODEL_ID} (OPTION 3)")
    print("="*80)

    passed_count = 0
    total = len(fixtures)

    for i, fix in enumerate(fixtures, 1):
        print(f"\n[{i}/{total}] Testing: {fix['id']} - {fix['description']}")
        result = classify_message(fix)

        expected_intent = fix["expected_intent"]
        predicted_intent = result["predicted_intent"]
        is_pass = (expected_intent == predicted_intent)
        if is_pass:
            passed_count += 1

        status_icon = "✅ PASS" if is_pass else "❌ FAIL"
        print(f"  Result: {status_icon}")
        print(f"  Expected Intent:  {expected_intent}")
        print(f"  Predicted Intent: {predicted_intent}")
        print(f"  Extracted Phrase: {result['time_phrase']}")
        print(f"  Summary:          {result['summary']}")
        if not is_pass:
            print(f"  [Raw LLM Output]:\n{result['raw_output']}")

    accuracy = (passed_count / total) * 100
    print("\n" + "="*80)
    print(f"EVALUATION SUMMARY: {passed_count}/{total} Passed ({accuracy:.1f}% Accuracy)")
    print("="*80 + "\n")

if __name__ == "__main__":
    run_evaluation()
