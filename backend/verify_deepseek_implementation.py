"""
Verification script to check if DeepSeek implementation is correct
Run this after restarting the backend server
"""
import sys
import importlib.util

def check_implementation():
    """Check if DeepSeek implementation uses requests library, not OpenAI SDK"""
    
    print("=" * 60)
    print("DeepSeek Implementation Verification")
    print("=" * 60)
    print()
    
    # Check ai_post_processor.py
    print("[1] Checking ai_post_processor.py...")
    try:
        spec = importlib.util.spec_from_file_location(
            "ai_post_processor",
            "app/services/ai_post_processor.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Check for OpenAI SDK imports
        source = open("app/services/ai_post_processor.py", "r", encoding="utf-8").read()
        
        if "from openai" in source or "import openai" in source:
            print("  [X] ERROR: Still using OpenAI SDK!")
            print("     Found: 'from openai' or 'import openai'")
            return False
        
        if "OpenAI(" in source:
            print("  [X] ERROR: Still using OpenAI client!")
            print("     Found: 'OpenAI('")
            return False
        
        if "_get_client" in source and "def _get_client" in source:
            print("  [X] ERROR: Still using old _get_client method!")
            print("     Found: 'def _get_client'")
            return False
        
        if "AiEnhanceClient" not in source:
            print("  [X] ERROR: AiEnhanceClient not found!")
            return False
        
        if "from app.services.ai_enhance_client import" not in source:
            print("  [X] ERROR: Not importing AiEnhanceClient!")
            return False
        
        print("  [OK] ai_post_processor.py looks correct")
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        return False
    
    # Check ai_enhance_client.py
    print("[2] Checking ai_enhance_client.py...")
    try:
        source = open("app/services/ai_enhance_client.py", "r", encoding="utf-8").read()
        
        if "import requests" not in source:
            print("  [X] ERROR: Not using requests library!")
            return False
        
        if "from openai" in source or "import openai" in source:
            print("  [X] ERROR: Still using OpenAI SDK!")
            return False
        
        if "class AiEnhanceClient" not in source:
            print("  [X] ERROR: AiEnhanceClient class not found!")
            return False
        
        print("  [OK] ai_enhance_client.py looks correct")
        
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        return False
    
    # Check main.py
    print("[3] Checking main.py...")
    try:
        source = open("app/main.py", "r", encoding="utf-8").read()
        
        if "_patch_httpx" in source or "monkey patch" in source.lower():
            print("  [!] WARNING: Still has monkey patch code (should be removed)")
            print("     This is not critical, but should be cleaned up")
        
        print("  [OK] main.py looks correct")
        
    except Exception as e:
        print(f"  [X] ERROR: {e}")
        return False
    
    print()
    print("=" * 60)
    print("[OK] All checks passed! Implementation looks correct.")
    print("=" * 60)
    print()
    print("[!] IMPORTANT: Make sure to restart the backend server!")
    print("   The server must be restarted to load the new code.")
    print()
    
    return True

if __name__ == "__main__":
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    success = check_implementation()
    sys.exit(0 if success else 1)
