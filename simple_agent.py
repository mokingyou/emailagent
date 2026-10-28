import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END

# 1. Initialize the Open-Source Model via Hugging Face
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"

print(f"Loading {MODEL_ID} into memory on CPU...")

# FIX: Force CPU to avoid the PyTorch MPS matrix multiplication bug
device = "cpu" 

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID, 
    torch_dtype=torch.float32, # CPU prefers float32
).to(device)

pipe = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=50,
    device=device
)

def local_llm(prompt: str) -> str:
    """Passes the prompt to the local open-source model and extracts the response."""
    messages = [
        {"role": "system", "content": "You are a concise AI assistant managing emails."},
        {"role": "user", "content": prompt}
    ]
    formatted_prompt = pipe.tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    outputs = pipe(formatted_prompt)
    return outputs[0]["generated_text"][len(formatted_prompt):].strip()

# 2. State Schema
class EmailState(TypedDict):
    email_text: str
    extracted_info: str
    category: str
    draft: str

# 3. Nodes (The Agents)
def reader_node(state: EmailState):
    print("-> Reading email...")
    prompt = f"Extract the core issue and sender type from this email in 1 sentence: '{state['email_text']}'"
    return {"extracted_info": local_llm(prompt)}

def decider_node(state: EmailState):
    print("-> Deciding category...")
    prompt = f"Categorize this issue as exactly one word (support, finance, or forward): '{state['extracted_info']}'"
    return {"category": local_llm(prompt).lower()}

def drafter_node(state: EmailState):
    print("-> Drafting response...")
    prompt = f"Write a short, polite email reply addressing this issue: '{state['extracted_info']}'"
    return {"draft": local_llm(prompt)}

def forwarder_node(state: EmailState):
    print("-> Forwarding...")
    return {"draft": "Fwd: Needs manual review."}

# 4. Routing Logic
def route_email(state: EmailState) -> Literal["drafter_node", "forwarder_node", END]:
    category = state.get("category", "")
    if "support" in category or "finance" in category:
        return "drafter_node"
    return "forwarder_node"

# 5. Graph Construction
workflow = StateGraph(EmailState)

workflow.add_node("reader_node", reader_node)
workflow.add_node("decider_node", decider_node)
workflow.add_node("drafter_node", drafter_node)
workflow.add_node("forwarder_node", forwarder_node)

workflow.set_entry_point("reader_node")
workflow.add_edge("reader_node", "decider_node")
workflow.add_conditional_edges("decider_node", route_email)
workflow.add_edge("drafter_node", END)
workflow.add_edge("forwarder_node", END)

app = workflow.compile()

# Execution
if __name__ == "__main__":
    initial_state = {"email_text": "Hey, I was double charged on my last invoice and I am very upset. Please fix this."}
    print("Starting agent workflow...\n")
    
    result = app.invoke(initial_state)
    
    print("\n=== AGENT RESULTS ===")
    print(f"Extracted Info: {result.get('extracted_info')}")
    print(f"Category: {result.get('category')}")
    print(f"Draft Action: \n{result.get('draft')}")