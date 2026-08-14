#!/bin/bash
# Save as find_big_deletions_enhanced.sh

FILE="${1:-<filename>}"
N="${2:-100}"
THRESHOLD="${3:-10}"

echo "📊 Analyzing last $N commits for: $FILE"
echo "🔴 Marking commits with deletions > $THRESHOLD lines"
echo ""

# Store all deletions for summary
declare -a all_deletions
declare -a all_additions
big_deletions=0
total_deletions=0
total_additions=0

echo "=================================================================================="
printf "%-12s %-10s %-8s %-8s %-20s %s\n" "COMMIT" "DATE" "+ADDED" "-DEL" "AUTHOR" "MESSAGE"
echo "=================================================================================="

git log -n $N --format="%h|%ci|%an|%s" -- $FILE | while IFS='|' read hash date author msg; do
    # Get stats
    stats=$(git show --numstat --format="" $hash -- $FILE)
    
    if [ -n "$stats" ]; then
        added=$(echo "$stats" | awk '{print $1}')
        deleted=$(echo "$stats" | awk '{print $2}')
        [ "$added" = "-" ] && added=0
        [ "$deleted" = "-" ] && deleted=0
    else
        added=0
        deleted=0
    fi
    
    short_date=$(echo "$date" | cut -d' ' -f1)
    
    # Accumulate for summary
    total_additions=$((total_additions + added))
    total_deletions=$((total_deletions + deleted))
    
    # Highlight if deletion count exceeds threshold
    if [ "$deleted" -gt "$THRESHOLD" ]; then
        big_deletions=$((big_deletions + 1))
        # Red with bold
        printf "\033[31;1m"  # Bold red
        printf "%-12s %-10s %+8s %-8s %-20s %s\n" \
            "$hash" "$short_date" "$added" "$deleted" "${author:0:20}" "${msg:0:50}"
        printf "\033[0m"
        
        # Show the actual diff for this commit (optional)
        echo "  📝 Changes:"
        git show --stat $hash -- $FILE | grep -E "files? changed" | sed 's/^/  /'
        echo ""
    else
        printf "%-12s %-10s %+8s %-8s %-20s %s\n" \
            "$hash" "$short_date" "$added" "$deleted" "${author:0:20}" "${msg:0:50}"
    fi
done

echo "=================================================================================="
echo ""
echo "📈 Summary:"
echo "  - Commits analyzed: $N"
echo "  - Total additions: $total_additions"
echo "  - Total deletions: $total_deletions"
echo "  - Net change: $((total_additions - total_deletions))"
echo "  - 🔴 Big deletions (>$THRESHOLD lines): $big_deletions"
