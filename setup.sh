#!/bin/bash
# This script automates the setup of the development environment for the project on Linux and macOS.
set -e

echo "--- Starting Project Setup ---"

# --- Check for Python ---
# Verifies that Python 3 is installed and available in the system's PATH.
# The project requires Python 3.9 or higher.
if ! command -v python3 &> /dev/null
then
    echo "ERROR: python3 could not be found. Please install Python 3.9 or higher."
    exit 1
fi
echo "Python 3 found."

# --- Check for uv and install if missing ---
# 'uv' is a fast Python package installer and resolver, used here to manage the virtual environment
# and dependencies. If 'uv' is not found, this block attempts to install it.
if ! command -v uv &> /dev/null; then
    echo "'uv' not found. Attempting to install it..."

    # Try to download and run the 'uv' installation script using curl or wget.
    if command -v curl &> /dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget &> /dev/null; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "ERROR: Neither 'curl' nor 'wget' is available to download 'uv'."
        exit 1
    fi

    # The 'uv' installer places the binary in ~/.local/bin. This line adds that
    # directory to the PATH for the current script session to ensure 'uv' can be found.
    export PATH="$HOME/.local/bin:$PATH"

    # Verify that the installation was successful.
    if ! command -v uv &> /dev/null; then
        echo "ERROR: 'uv' installation failed or is not in PATH. Please ensure '~/.local/bin' is in your PATH."
        exit 1
    fi

    echo "'uv' installed successfully."
else
    echo "'uv' is already installed."
fi

# --- Create Virtual Environment with uv ---
# A virtual environment isolates the project's dependencies from the system's Python packages.
# This block creates a virtual environment in a directory named '.venv' if it doesn't already exist.
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment '$VENV_DIR' already exists. Skipping creation."
else
    echo "Creating virtual environment in '$VENV_DIR' using uv..."
    uv venv
    echo "Virtual environment created."
fi

# --- Activate Virtual Environment and Install Dependencies ---
# Activates the newly created virtual environment and installs all required packages.
echo "Activating virtual environment and installing dependencies..."
source $VENV_DIR/bin/activate

# Installs both production and development dependencies defined in 'pyproject.toml'.
echo "Installing dependencies from pyproject.toml using uv..."
uv pip install -e ".[dev]"
echo "Dependencies installed successfully."

# --- Set up pre-commit hooks ---
# Pre-commit hooks run automated checks (e.g., linting, formatting) on the code
# before a commit is made. This ensures code quality and consistency.
echo "Installing pre-commit hooks..."
pre-commit install
echo "Pre-commit hooks installed."

# --- Final Instructions ---
echo ""
echo "--- Project Setup Complete ---"
echo "The virtual environment is ready and dependencies are installed."
echo "To activate the virtual environment in your shell, run:"
echo "source .venv/bin/activate"
echo "--------------------------------"
