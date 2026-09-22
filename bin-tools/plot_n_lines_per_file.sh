#!/bin/bash
# Save as plot_lines.sh and make executable: chmod +x plot_lines.sh

FILE="${1:-<filename>}"  # Pass filename as argument
N="${2:-100}"            # Number of commits (default 100)

echo "# Date,Commit,Author,Total_Lines,Added,Deleted"

git log -n $N --format="%ci|%h|%an" -- $FILE | while IFS='|' read date hash author; do
    # Get file content at this commit
    content=$(git show $hash:$FILE 2>/dev/null)
    if [ $? -eq 0 ]; then
        total=$(echo "$content" | wc -l)
    else
        total=0
    fi
    
    # Get stats
    stats=$(git show --numstat --format="" $hash -- $FILE)
    if [ -n "$stats" ]; then
        added=$(echo "$stats" | awk '{print $1}')
        deleted=$(echo "$stats" | awk '{print $2}')
    else
        added=0
        deleted=0
    fi
    
    echo "$date,$hash,\"$author\",$total,$added,$deleted"
done
