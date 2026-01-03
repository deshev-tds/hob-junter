import json
import os
import sys
import time

def print_header(text):
    print(f"\n\033[1;36m{'='*60}")
    print(f" {text}")
    print(f"{'='*60}\033[0m")

def print_info(text):
    print(f" ℹ️  \033[37m{text}\033[0m")

def print_warn(text):
    print(f" ⚠️  \033[33m{text}\033[0m")

def prompt_user(question, default=None):
    if default:
        prompt_text = f"\n👉 {question} \033[90m[{default}]\033[0m: "
    else:
        prompt_text = f"\n👉 {question}: "
    
    val = input(prompt_text).strip()
    if not val and default is not None:
        return default
    return val

def run_wizard():
    print_header("HOB-JUNTER: INITIALIZATION WIZARD")
    print("Welcome, operator *salutes*. Let's configure your autonomous job hunter.")
    print("We will set up your target, your weapons (AI), and your reporting.")

    # --- 1. TARGET CV ---
    print_header("STEP 1: THE ASSET (Your CV)")
    print_info("I need the path to your CV (PDF format - aim for cleaner text, although we do heavy OCR and should be able to read it regardless; with that in mind, not all automated systems where your CV might lend are doing the same ;) ).")
    print_info("Tip: You can drag and drop the file into this terminal window and absolute path should be captured automatically. No promises, this behaviour differs across systems.")
    
    while True:
        cv_path = prompt_user("Path to CV PDF", default="CV.pdf")
        # Remove artifacts from drag-and-drop
        cv_path = cv_path.replace('"', '').replace("'", "").replace("\\ ", " ").strip()
        
        if os.path.exists(cv_path):
            print(f"    Found: {cv_path}")
            break
        print_warn(f"File not found at: {cv_path}")
        if prompt_user("Try again?", default="y").lower() != 'y':
            sys.exit(1)

    # --- 2. SEARCH INTELLIGENCE ---
    print_header("STEP 2: TARGETING STRATEGY")
    print_info("You can provide a specific Hiring.Cafe search URL, OR leave it empty.")
    print_info("If EMPTY, the AI will analyze your CV and build the best search query automatically. I would rather let it do its magic!")
    
    search_url = prompt_user("Search URL (Press Enter for AI Auto-Pilot)", default="")
    if not search_url:
        print("    Mode: AI Auto-Pilot engaged.")
    else:
        print("    Mode: Manual Override.")

    # --- 3. SCORING ENGINE ---
    print_header("STEP 3: THE BRAIN (Scoring Engine)")
    print_info("Who judges the candidates? Your local machine or OpenAI?")
    
    print("\n[local]") 
    print("  - Cost: FREE")
    print("  - Privacy: High (CV stays on machine)")
    print("  - Req: LMStudio/Ollama running a model on port 1234")
    
    print("\n[openai]")
    print("  - Cost: $$$ (Uses tokens)")
    print("  - Intel: Smarter, better reasoning")
    print("  - Req: OPENAI_API_KEY in .env file or as an env variable - you know, the whole EXPORT thing.")

    scoring_mode = prompt_user("Choose Engine (local/openai)", default="local").lower()
    if scoring_mode not in ["local", "openai"]:
        scoring_mode = "local"
    
    if scoring_mode == "openai":
        print_warn("Ensure you have set OPENAI_API_KEY in your environment variables!")

    # --- 4. SIGNAL FILTER (THRESHOLD) ---
    print_header("STEP 4: SIGNAL-TO-NOISE RATIO")
    print_info("The Threshold determines how picky the bot is (0-100).")
    print("   < 60: Desperation Mode. Lots of garbage.")
    print("   65:   Wide Net. Expect false positives.")
    print("   75:   The Sweet Spot. Good balance.")
    print("   85+:  Unicorn Hunting. You might miss hidden gems.")
    
    while True:
        try:
            threshold_str = prompt_user("Minimum Score", default="75")
            threshold = int(threshold_str)
            if 0 <= threshold <= 100:
                break
            print_warn("Please enter a number between 0 and 100.")
        except ValueError:
            print_warn("Invalid number.")

    # --- 5. CRM INTEGRATION (GOOGLE SHEETS) ---
    print_header("STEP 5: CRM (Google Sheets)")
    print_info("This enables the automatic tracking dashboard.")
    
    setup_sheets = prompt_user("Do you want to set up Google Sheets now?", default="y").lower()
    spreadsheet_id = ""
    
    if setup_sheets in ("y", "yes", "1"):
        print("\n📝 INSTRUCTIONS (Read carefully):")
        print("1. Go to: https://console.cloud.google.com/")
        print("2. Create a New Project (e.g., 'Job-Hunter').")
        print("3. Search for & ENABLE these two APIs:")
        print("   - Google Sheets API")
        print("   - Google Drive API")
        print("4. Go to Credentials -> Create Credentials -> **Service Account**.")
        print("5. Name it 'bot-user', click Done.")
        print("6. Click the new email (bot-user@...), go to **KEYS** tab.")
        print("7. Add Key -> Create New Key -> **JSON**. It will download.")
        print("8. Rename that file to 'service_account.json' and put it in this folder.")
        print("9. Open the JSON file, copy the 'client_email'.")
        print("10. Share your Google Sheet with that email (Give 'Editor' access).")
        
        input("\nPress Enter when you have done these steps...")
        
        spreadsheet_id = prompt_user("Paste your Google Sheet ID (from the URL)")
        
        # Check for the key file
        if not os.path.exists("service_account.json"):
            print_warn("I don't see 'service_account.json' in this folder yet.")
            print_warn("Please make sure to save it here before running the bot.")
    else:
        print("   Skipping Sheets integration.")

    # --- 6. DEBUG & PATHS ---
    # Enforcing constraints: Debug is ON.
    debug = True 
    print_header("STEP 6: SYSTEM INTERNALS")
    print("    Debug Mode: ENABLED (For full transparency)")
    
    # Defaults for paths
    db_path = "jobs.db"
    creds_path = "service_account.json"
    
    # --- FINAL CONFIG ---
    config = {
        "cv_path": cv_path,
        "search_url": search_url,
        "spreadsheet_id": spreadsheet_id,
        "threshold": threshold,
        "debug": debug,
        "scoring_mode": scoring_mode,
        "db_path": db_path,
        "google_creds_path": creds_path
    }

    try:
        with open("inputs.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        
        print_header("SETUP COMPLETE")
        print(" Configuration saved to 'inputs.json'")
        if scoring_mode == "local":
             print("  REMINDER: Make sure LMStudio/Ollama is running on port 1234!")
        if spreadsheet_id:
             print(" CRM: Active")
        
        print("\n Ready to launch. Run: python main.py")
        
    except Exception as e:
        print_warn(f"Failed to save config: {e}")

if __name__ == "__main__":
    run_wizard()