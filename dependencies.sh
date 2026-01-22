#!/bin/bash

echo "Installs bibtool based on OS"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored messages
print_color() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to detect platform
detect_platform() {
    case "$(uname -s)" in
        Linux*)     platform="Linux" ;;
        Darwin*)    platform="Mac" ;;
        CYGWIN*|MINGW*|MSYS*) platform="Windows" ;;
        *)          platform="Unknown" ;;
    esac
    echo $platform
}

# Function to detect Linux distribution
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo $ID
    elif type lsb_release >/dev/null 2>&1; then
        echo $(lsb_release -si | tr '[:upper:]' '[:lower:]')
    else
        echo "unknown"
    fi
}

# Main installation function
install_bibtool() {
    local platform=$(detect_platform)
    
    print_color $BLUE "Detected platform: $platform"
    
    case $platform in
        Linux)
            local distro=$(detect_distro)
            print_color $BLUE "Detected distribution: $distro"
            
            case $distro in
                ubuntu|debian|linuxmint|pop|elementary)
                    print_color $YELLOW "Installing bibtool using apt..."
                    sudo apt update
                    if sudo apt install -y bibtool; then
                        print_color $GREEN "✓ bibtool installed successfully!"
                    else
                        print_color $RED "✗ Failed to install bibtool via apt"
                        exit 1
                    fi
                    ;;
                fedora|centos|rhel|rocky|almalinux)
                    print_color $YELLOW "Installing bibtool using dnf/yum..."
                    # Try dnf first, fall back to yum
                    if command -v dnf >/dev/null 2>&1; then
                        sudo dnf install -y bibtool
                    elif command -v yum >/dev/null 2>&1; then
                        sudo yum install -y bibtool
                    else
                        print_color $RED "✗ Neither dnf nor yum package manager found"
                        exit 1
                    fi
                    
                    if [ $? -eq 0 ]; then
                        print_color $GREEN "✓ bibtool installed successfully!"
                    else
                        print_color $RED "✗ Failed to install bibtool"
                        exit 1
                    fi
                    ;;
                arch|manjaro|endeavouros)
                    print_color $YELLOW "Installing bibtool using pacman..."
                    sudo pacman -Sy --noconfirm bibtool
                    if [ $? -eq 0 ]; then
                        print_color $GREEN "✓ bibtool installed successfully!"
                    else
                        print_color $RED "✗ Failed to install bibtool via pacman"
                        exit 1
                    fi
                    ;;
                opensuse*|suse)
                    print_color $YELLOW "Installing bibtool using zypper..."
                    sudo zypper install -y bibtool
                    if [ $? -eq 0 ]; then
                        print_color $GREEN "✓ bibtool installed successfully!"
                    else
                        print_color $RED "✗ Failed to install bibtool via zypper"
                        exit 1
                    fi
                    ;;
                *)
                    print_color $RED "Unsupported Linux distribution: $distro"
                    print_color $YELLOW "Please install bibtool manually for your distribution"
                    exit 1
                    ;;
            esac
            ;;
        
        Mac)
            print_color $YELLOW "Installing bibtool using Homebrew..."
            
            # Check if Homebrew is installed
            if ! command -v brew >/dev/null 2>&1; then
                print_color $RED "✗ Homebrew not found!"
                print_color $YELLOW "Installing Homebrew first..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                
                # Add Homebrew to PATH for Apple Silicon Macs
                if [[ $(uname -m) == 'arm64' ]]; then
                    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
                    eval "$(/opt/homebrew/bin/brew shellenv)"
                fi
            fi
            
            # Install bib-tool (note: Homebrew uses bib-tool, not bibtool)
            if brew install bib-tool; then
                print_color $GREEN "✓ bibtool is alread installed !"
                print_color $YELLOW "Note: On macOS, the packages is called 'bib-tool' not 'bibtool'"
            else
                print_color $RED "✗ Failed to install bib-tool via Homebrew"
                exit 1
            fi
            ;;
        
        Windows)
            print_color $YELLOW "Windows detected - trying different methods..."
            
            # Check for WSL
            if grep -q microsoft /proc/version 2>/dev/null; then
                print_color $BLUE "WSL (Windows Subsystem for Linux) detected"
                # Re-run in the Linux context
                print_color $YELLOW "Running Linux installation method..."
                install_bibtool
                return
            fi
            
            # Check for Chocolatey
            if command -v choco >/dev/null 2>&1; then
                print_color $YELLOW "Installing bibtool using Chocolatey..."
                choco install bibtool -y
                if [ $? -eq 0 ]; then
                    print_color $GREEN "✓ bibtool installed successfully!"
                    return
                fi
            fi
            
            # Check for Scoop
            if command -v scoop >/dev/null 2>&1; then
                print_color $YELLOW "Installing bibtool using Scoop..."
                scoop install bibtool
                if [ $? -eq 0 ]; then
                    print_color $GREEN "✓ bibtool installed successfully!"
                    return
                fi
            fi
            
            # Check for Cygwin or MSYS2
            if command -v pacman >/dev/null 2>&1 && [ -d /cygdrive ] || [ -d /msys2 ]; then
                print_color $YELLOW "Cygwin/MSYS2 detected - using pacman..."
                pacman -Sy --noconfirm bibtool
                if [ $? -eq 0 ]; then
                    print_color $GREEN "✓ bibtool installed successfully!"
                    return
                fi
            fi
            
            print_color $RED "✗ Could not find a package manager for Windows"
            print_color $YELLOW "Please install manually:"
            print_color $YELLOW "1. For WSL: Run this script from WSL"
            print_color $YELLOW "2. Install Chocolatey (choco install bibtool)"
            print_color $YELLOW "3. Install Scoop (scoop install bibtool)"
            print_color $YELLOW "4. Use Cygwin or MSYS2 package manager"
            exit 1
            ;;
        
        *)
            print_color $RED "Unsupported platform: $platform"
            print_color $YELLOW "Please install bibtool manually"
            exit 1
            ;;
    esac
    
    # Verify installation
    print_color $BLUE "Verifying installation..."
    if [ "$platform" = "Mac" ]; then
        if command -v bibtool >/dev/null 2>&1; then
            print_color $GREEN "✓ Verification passed: bibtool is available"
        else
            print_color $RED "✗ bibtool not found after installation"
            exit 1
        fi
    else
        if command -v bibtool >/dev/null 2>&1; then
            print_color $GREEN "✓ Verification passed: bibtool is available"
        else
            print_color $RED "✗ bibtool not found after installation"
            exit 1
        fi
    fi
}

# Check if running as root/sudo
if [ "$EUID" -eq 0 ]; then 
    print_color $YELLOW "Warning: Running as root. Some package managers don't like this."
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_color $RED "Installation cancelled"
        exit 1
    fi
fi

# Run installation
print_color $BLUE "Starting bibtool installation..."
install_bibtool
print_color $GREEN "🎉 Installation completed successfully"
print_color $GREEN "🎉 You can now format BibTeX files according to the FLOSS-research-bibliography-collection style"



