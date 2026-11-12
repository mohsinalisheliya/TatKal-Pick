#!/usr/bin/env bash

# ============================================================
# CONFIGURATION
# ============================================================
# Time interval in seconds:
# 60 = 1 minute | 120 = 2 minutes | 300 = 5 minutes
CHECK_INTERVAL=05
REMOTE="origin"
   
# Terminal Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Graceful exit on [Ctrl + C]
trap ctrl_c INT
function ctrl_c() {
    echo -e "\n${YELLOW}🛑 Auto-push daemon band ho gaya hai. Happy coding!${NC}"
    exit 0
}

# Check if current folder is a Git repository
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo -e "${RED}❌ Error: Yeh folder ek Git repository nahi hai!${NC}"
    echo "Pehle 'git init' karein ya project repository ke andar move karein."
    exit 1
fi

echo -e "${CYAN}====================================================${NC}"
echo -e "${GREEN}🚀 Auto-Push Daemon chalu ho gaya hai!${NC}"
echo -e "⏳ Check Interval : ${YELLOW}${CHECK_INTERVAL} seconds${NC}"
echo -e "🛑 Rokne ke liye   : Terminal mein [Ctrl + C] dabayein"
echo -e "${CYAN}====================================================${NC}"

while true; do
    # Auto-detect current active branch (main, master, dev, etc.)
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")

    # Check for any unstaged, staged, or untracked changes
    if [[ -n $(git status --porcelain) ]]; then
        TIME_STAMP=$(date +"%d-%b-%Y %I:%M:%S %p")
        echo -e "\n[${TIME_STAMP}] ${YELLOW}📝 Changes detect huye! Staging aur commit ho raha hai...${NC}"

        git add .
        git commit -m "auto-save: progress update ($TIME_STAMP)" >/dev/null 2>&1

        echo -e "[${TIME_STAMP}] 🚀 Pushing to ${CYAN}${REMOTE}/${CURRENT_BRANCH}${NC}..."
        
        # Attempt push with error protection
        if git push "$REMOTE" "$CURRENT_BRANCH" >/dev/null 2>&1; then
            echo -e "[${TIME_STAMP}] ${GREEN}✅ GitHub ke sath successfully sync ho gaya!${NC}"
        else
            echo -e "[${TIME_STAMP}] ${RED}⚠️ Push fail hua (Network issue ya remote changes out of sync). Agle cycle mein dubara try karega.${NC}"
        fi
    else
        echo -e "[$(date +"%I:%M:%S %p")] 💤 Koi naya change nahi mila, idle..."
    fi

    sleep "$CHECK_INTERVAL"
done   