#!/bin/bash

# format_all_with_bibtool.sh
# Formats all .bib files in the directory using bibtool

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'
DIM='\033[2m'

# Configuration
CONFIG_FILE="bibtoolrsc.rsc"
BACKUP_DIR="./bibtool_backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# Global flags for "yes to all" and "no to all"
YES_TO_ALL=false
NO_TO_ALL=false

# Function to print colored messages
print_info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "\n${BLUE}${BOLD}$1${NC}"
}

# Function to check if bibtool is installed
check_dependencies() {
    if ! command -v bibtool &> /dev/null; then
        print_error "bibtool is not installed or not in PATH."
        print_info "Please install bibtool to use this script."
        exit 1
    fi
    
    if [ ! -f "$CONFIG_FILE" ]; then
        print_warning "Configuration file '$CONFIG_FILE' not found in current directory."
        print_info "Will proceed without custom configuration file."
        CONFIG_FILE=""
    else
        print_success "Found configuration file: $CONFIG_FILE"
    fi
}

# Function to create backup directory
setup_backup_dir() {
    if [ ! -d "$BACKUP_DIR" ]; then
        mkdir -p "$BACKUP_DIR"
        print_success "Created backup directory: $BACKUP_DIR"
    fi
}

# Function to create backup of a file
create_backup() {
    local file="$1"
    local backup_file="$BACKUP_DIR/${file}.backup_$TIMESTAMP"
    
    cp "$file" "$backup_file"
    
    if [ $? -eq 0 ]; then
        print_success "Backup created: $backup_file"
    else
        print_error "Failed to create backup for $file"
        return 1
    fi
    return 0
}

# Function to process a single .bib file
process_file() {
    local file="$1"
    
    print_header "Processing: $file"
    
    # Create backup
    print_info "Creating backup..."
    create_backup "$file" || return 1
    
    # Format file with bibtool
    print_info "Formatting with bibtool..."
    
    if [ -n "$CONFIG_FILE" ]; then
        bibtool -v -d -k -r "$CONFIG_FILE" -F "$file" -o "$file"
    else
        bibtool -v -d -k -F "$file" -o "$file"
    fi
    
    if [ $? -eq 0 ]; then
        print_success "Successfully formatted: $file"
        return 0
    else
        print_error "Failed to format: $file"
        return 1
    fi
}

# Function to handle user confirmation
get_confirmation() {
    local file="$1"
    
    if $YES_TO_ALL; then
        return 0
    fi
    
    if $NO_TO_ALL; then
        return 1
    fi
    
    echo -e "${YELLOW}Process file:${NC} ${BOLD}$file${NC} ?"
    echo -e "  ${GREEN}y${NC} - Yes, process this file"
    echo -e "  ${RED}n${NC} - No, skip this file"
    echo -e "  ${GREEN}a${NC} - Yes to ALL files"
    echo -e "  ${RED}q${NC} - No to ALL files (quit)"
    echo -en "${YELLOW}Your choice [y/n/a/q]: ${NC}"
    
    while true; do
        read -r -n1 choice
        case $choice in
            y|Y)
                echo
                return 0
                ;;
            n|N)
                echo
                return 1
                ;;
            a|A)
                echo
                YES_TO_ALL=true
                print_info "Will process ALL remaining files without confirmation."
                return 0
                ;;
            q|Q)
                echo
                NO_TO_ALL=true
                print_info "Skipping ALL remaining files."
                return 1
                ;;
            *)
                echo -e "\n${RED}Invalid choice. Please enter y, n, a, or q.${NC}"
                echo -en "${YELLOW}Your choice [y/n/a/q]: ${NC}"
                ;;
        esac
    done
}

# Main function
main() {
    print_header "BibTool Formatter"
    print_info "Script started at: $(date)"
    print_info "Backup directory: $BACKUP_DIR"
    print_info "Timestamp: $TIMESTAMP"
    
    # Check dependencies
    check_dependencies
    
    # Setup backup directory
    setup_backup_dir
    
    # Find all .bib files in current directory
    local bib_files=(*.bib)
    local total_files=${#bib_files[@]}
    local processed_count=0
    local skipped_count=0
    local error_count=0
    
    if [ $total_files -eq 0 ]; then
        print_warning "No .bib files found in current directory."
        exit 0
    fi
    
    print_info "Found $total_files .bib file(s) to process."
    
    # Process each .bib file
    for file in "${bib_files[@]}"; do
        if [ ! -f "$file" ]; then
            continue  # Skip if not a regular file
        fi
        
        # Get user confirmation
        if get_confirmation "$file"; then
            if process_file "$file"; then
                ((processed_count++))
            else
                ((error_count++))
            fi
        else
            print_info "Skipped: $file"
            ((skipped_count++))
        fi
        
        echo
    done
    
    # Summary
    print_header "Summary"
    echo -e "${GREEN}Processed:${NC} $processed_count file(s)"
    echo -e "${YELLOW}Skipped:${NC} $skipped_count file(s)"
    echo -e "${RED}Errors:${NC} $error_count file(s)"
    echo -e "${CYAN}Total:${NC} $total_files file(s)"
    echo
    print_info "Backups are stored in: $BACKUP_DIR"
    
    if [ $error_count -eq 0 ]; then
        print_success "Formatting completed successfully!"
    else
        print_warning "Formatting completed with $error_count error(s)."
    fi
}

# Run main function
main "$@"
