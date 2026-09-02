"""
Verify AI analysis configuration reads API key directly from config.
Run: python verify_ai_config.py
"""

import yaml
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

def verify_ai_config():
    """Verify AI analysis config has correct API key setup."""
    config_path = Path(__file__).parent / "config.yml"
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)
    
    ai_cfg = cfg.get('ai_analysis', {})
    enabled = ai_cfg.get('enabled', False)
    provider = ai_cfg.get('provider', '')
    api_key = ai_cfg.get('api_key_env', '')
    model = ai_cfg.get('model', '')
    base_url = ai_cfg.get('base_url', '')
    
    print("=" * 70)
    print("AI Analysis Configuration Verification")
    print("=" * 70)
    print(f"AI Enabled: {enabled}")
    print(f"Provider: {provider}")
    print(f"Model: {model}")
    print(f"Base URL: {base_url or 'N/A (using provider default)'}")
    print(f"API Key: {'***' + api_key[-8:] if len(api_key) > 8 else 'MISSING'}")
    print("=" * 70)
    
    # Validation
    issues = []
    if not enabled:
        issues.append("⚠️  AI analysis is DISABLED")
    if not api_key:
        issues.append("❌ API key is EMPTY - AI recommendations will not work")
    if provider == "openai_compatible" and not base_url:
        issues.append("⚠️  Provider is 'openai_compatible' but base_url is empty")
    
    if issues:
        print("\n⚠️  ISSUES FOUND:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("\n✅ AI analysis configuration is correct!")
        print("   • API key is configured (reading directly from config)")
        print("   • No environment variable lookup required")
        print("   • AI recommendations will be generated for top signals")
        return True


if __name__ == "__main__":
    success = verify_ai_config()
    
    if success:
        print("\n" + "=" * 70)
        print("Next steps:")
        print("1. Restart the bot: python app.py")
        print("2. Run a scan with signals (Grade A/B/C)")
        print("3. Check dashboard for AI recommendation badges (⭐ 🤖)")
        print("4. Hover over badges to see AI reasoning")
        print("=" * 70)
    
    exit(0 if success else 1)
