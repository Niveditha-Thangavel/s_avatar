#!/usr/bin/env python3
"""
test_stack.py — S2S Voice Avatar Stack Validation Script
Performs health checks on the running Docker stack (STT, Orchestrator, Frontend)
and displays configuration status and GPU VRAM usage.
"""

import sys
import urllib.request
import json
import subprocess
import time

# Colors
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
NC = "\033[0m"

# Configured target URLs
STT_HEALTH_URL = "http://localhost:8080/health"
ORCHESTRATOR_HEALTH_URL = "http://localhost:8765/health"
FRONTEND_URL = "http://localhost:3005"

def check_http_endpoint(name, url, expected_status=200, check_json=None):
    print(f"Checking {name} at {url} ... ", end="")
    sys.stdout.flush()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            status = response.status
            body = response.read().decode("utf-8")
            
            if status != expected_status:
                print(f"{RED}FAIL (HTTP {status}){NC}")
                return False, None
                
            if check_json:
                try:
                    data = json.loads(body)
                    success, msg = check_json(data)
                    if success:
                        print(f"{GREEN}OK{NC}")
                        return True, data
                    else:
                        print(f"{RED}FAIL ({msg}){NC}")
                        return False, data
                except json.JSONDecodeError:
                    print(f"{RED}FAIL (Invalid JSON response){NC}")
                    return False, None
            
            print(f"{GREEN}OK{NC}")
            return True, body
    except Exception as e:
        print(f"{RED}FAIL (Error: {e}){NC}")
        return False, None

def check_gpu():
    print(f"\n{CYAN}--- GPU / VRAM Status ---{NC}")
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True
        )
        lines = result.stdout.strip().split("\n")
        for idx, line in enumerate(lines):
            name, total, used, free = [x.strip() for x in line.split(",")]
            used_gb = float(used) / 1024.0
            total_gb = float(total) / 1024.0
            pct = (float(used) / float(total)) * 100
            color = GREEN if pct < 70 else (YELLOW if pct < 90 else RED)
            print(f"GPU [{idx}]: {name}")
            print(f"  VRAM: {color}{used_gb:.2f} GB / {total_gb:.2f} GB used ({pct:.1f}%){NC}")
    except (subprocess.SubprocessError, FileNotFoundError):
        print(f"{YELLOW}nvidia-smi utility not found or GPU drivers are inactive.{NC}")

def check_stt_json(data):
    if data.get("status") != "ok":
        return False, "status is not 'ok'"
    device = data.get("device", "unknown")
    if device != "cuda":
        return False, f"STT running on CPU ({device}) instead of GPU (cuda)"
    return True, ""

def check_orchestrator_json(data):
    if data.get("status") != "ok":
        return False, "status is not 'ok'"
    
    services = data.get("services", {})
    translator = services.get("translator", "")
    tts = services.get("tts", "")
    
    warnings = []
    if translator != "loaded":
        warnings.append(f"translator status: {translator}")
    if "omnivoice" not in tts.lower():
        warnings.append(f"TTS is degraded or fallback: {tts}")
        
    if warnings:
        return False, " / ".join(warnings)
    return True, ""

def main():
    print(f"{CYAN}===================================================={NC}")
    print(f"{CYAN}       S2S Voice Avatar Stack Validation Dashboard   {NC}")
    print(f"{CYAN}===================================================={NC}")
    
    stt_ok, stt_data = check_http_endpoint("Vexyl STT (Port 8080)", STT_HEALTH_URL, check_json=check_stt_json)
    orch_ok, orch_data = check_http_endpoint("Orchestrator (Port 8765)", ORCHESTRATOR_HEALTH_URL, check_json=check_orchestrator_json)
    front_ok, _ = check_http_endpoint("Frontend App (Port 3005)", FRONTEND_URL)
    
    # Print detailed stats
    print(f"\n{CYAN}--- Service Details ---{NC}")
    if stt_ok and stt_data:
        print(f"Vexyl STT:")
        print(f"  Model:       {stt_data.get('model')}")
        print(f"  Device:      {GREEN}{stt_data.get('device')}{NC}")
        print(f"  Decode Mode: {stt_data.get('decode_mode')}")
        print(f"  Sessions:    {stt_data.get('active_sessions')} active")
    
    if orch_ok and orch_data:
        models = orch_data.get("models", {})
        print(f"Orchestrator:")
        print(f"  EN-INDIC Model: {models.get('en_indic')}")
        print(f"  INDIC-EN Model: {models.get('indic_en')}")
        print(f"  TTS Model:      {models.get('tts')}")
        print(f"  TTS Status:     {GREEN}{orch_data.get('services', {}).get('tts')}{NC}")

    check_gpu()

    print(f"\n{CYAN}===================================================={NC}")
    if stt_ok and orch_ok and front_ok:
        print(f"Status: {GREEN}{BOLD}ALL SERVICES RUNNING PROPERLY (GPU ACTIVE) 🚀{NC}")
        sys.exit(0)
    else:
        print(f"Status: {RED}{BOLD}HEALTH CHECKS FAILED! Please review logs. ⚠️{NC}")
        sys.exit(1)

if __name__ == "__main__":
    main()
