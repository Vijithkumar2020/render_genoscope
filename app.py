from flask import Flask, request, render_template, jsonify
from playwright.sync_api import sync_playwright
from flask_cors import CORS
import pandas as pd
import json
from datetime import datetime
import time
import re
from collections import defaultdict
from waitress import serve
import os
import psutil
import requests
from bs4 import BeautifulSoup
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

print(f"Starting application with environment variables:")
print(f"PORT: {os.environ.get('PORT')}")
print(f"ENVIRONMENT: {os.environ.get('ENVIRONMENT')}")

def initialize_playwright():
    """Initialize Playwright and install necessary browsers"""
    print("Installing Playwright browsers...")
    try:
        # Install playwright browsers
        import subprocess
        subprocess.run(["playwright", "install", "chromium"], check=True)
        print("Playwright browsers installed successfully")
    except Exception as e:
        print(f"Error installing Playwright browsers: {e}")
        print("Continuing anyway, as the browser might already be installed")

# Call this function at the start of your app
initialize_playwright()

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configure CORS properly
if os.environ.get('ENVIRONMENT') == 'development':
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    logger.info("CORS configured for development (all origins allowed)")
else:
    # For production, specify allowed origins
    allowed_origins = os.environ.get('ALLOWED_ORIGINS', '').split(',')
    CORS(app, resources={r"/*": {"origins": allowed_origins}}, supports_credentials=True)
    logger.info(f"CORS configured for production with origins: {allowed_origins}")

def check_system_resources():
    """
    Check available system resources to determine which scraping method to use.
    Returns True if resources are sufficient for Playwright, False otherwise.
    """
    try:
        # Check available memory (in MB)
        available_memory = psutil.virtual_memory().available / (1024 * 1024)
        
        # Check CPU load
        cpu_percent = psutil.cpu_percent(interval=0.5)
        
        # Check if we're in a container with limited resources
        is_container = os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')
        
        # Check environment flags
        force_lightweight = os.environ.get('FORCE_LIGHTWEIGHT', '').lower() in ('true', '1', 'yes')
        force_playwright = os.environ.get('FORCE_PLAYWRIGHT', '').lower() in ('true', '1', 'yes')
        
        if force_lightweight:
            logger.info("Lightweight mode forced by environment variable")
            return False
        
        if force_playwright:
            logger.info("Playwright mode forced by environment variable")
            return True
        
        # Determine if resources are sufficient for Playwright
        has_sufficient_resources = (available_memory > 300 and cpu_percent < 80)
        
        # In containers, be more conservative
        if is_container:
            has_sufficient_resources = (available_memory > 500 and cpu_percent < 60)
        
        logger.info(f"Resource check: Memory: {available_memory:.1f}MB, CPU: {cpu_percent:.1f}%, Container: {is_container}")
        logger.info(f"Using {'Playwright' if has_sufficient_resources else 'BeautifulSoup'} based on resources")
        
        return has_sufficient_resources
        
    except Exception as e:
        # If we can't check resources, default to lightweight method
        logger.warning(f"Error checking resources: {e}. Defaulting to lightweight method.")
        return False

def extract_clinvar_data_playwright(url):
    """
    Extract genetic variant data from a ClinVar page using Playwright with optimizations.
    """
    start_time = time.time()
    logger.info(f"Starting Playwright extraction for {url}")
    
    with sync_playwright() as p:
        # Launch browser with memory-specific optimizations
        browser_args = [
            '--disable-dev-shm-usage',
            '--disable-setuid-sandbox',
            '--no-sandbox',
            '--single-process',
            '--js-flags=--max-old-space-size=128',  # Limit JS heap size
            '--remote-debugging-port=0'
        ]
        
        try:
            browser = p.chromium.launch(
                headless=True,
                args=browser_args,
                timeout=30000
            )
            
            context = browser.new_context(
                viewport={"width": 800, "height": 600},  # Smaller viewport to use less memory
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            page = context.new_page()
            
            # Set strict timeout and handle failure gracefully
            page.set_default_timeout(15000)  # 15 seconds max for operations
            
            logger.info(f"Navigating to {url}")
            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            
            if not response or response.status >= 400:
                logger.warning(f"Failed to load page properly. Status: {response.status if response else 'No response'}")
                browser.close()
                return {"error": "Failed to load page"}
            
            # Extract data
            logger.info("Extracting data with Playwright...")
            
            data = {
                "variant_summary": {},
                "submissions": [],
                "molecular_consequences": [],
                "identifiers": {},
                "gene_info": []
            }
            
            try:
                # Basic details
                data["variant_summary"]["title"] = page.title()
                
                # Extract variant name
                variant_header = page.query_selector("h1")
                if variant_header:
                    data["variant_summary"]["variant_name"] = variant_header.inner_text().strip()
                
                # Extract clinical significance if present
                clin_sig_element = page.query_selector(".clinvar_review, .clinical_significance")
                if clin_sig_element:
                    data["variant_summary"]["clinical_significance"] = clin_sig_element.inner_text().strip()
                
                # Extract tables data
                tables = page.query_selector_all("table")
                for table in tables[:3]:  # Limit to first 3 tables
                    rows = table.query_selector_all("tr")
                    for row in rows[:10]:  # Limit rows per table
                        cells = row.query_selector_all("td, th")
                        if len(cells) >= 2:
                            key = cells[0].inner_text().strip().replace(":", "")
                            value = cells[1].inner_text().strip()
                            if key and value and len(key) < 100:
                                data["identifiers"][key] = value
                
                # Extract gene information
                gene_elements = page.query_selector_all('a[href*="/gene/"]')
                for gene_element in gene_elements[:3]:  # Limit to first 3 genes
                    gene_name = gene_element.inner_text().strip()
                    if gene_name:
                        gene_href = gene_element.get_attribute('href')
                        gene_id = gene_href.split('/')[-1].split('?')[0] if gene_href else None
                        data["gene_info"].append({
                            "gene_name": gene_name,
                            "gene_id": gene_id
                        })
                
            except Exception as e:
                logger.error(f"Error during Playwright extraction: {e}")
            
            browser.close()
            elapsed_time = time.time() - start_time
            logger.info(f"Playwright extraction completed in {elapsed_time:.2f} seconds")
            return data
            
        except Exception as e:
            logger.error(f"Playwright operation failed: {e}")
            # Ensure browser is closed even if something fails
            try:
                if 'browser' in locals():
                    browser.close()
            except Exception as be:
                logger.error(f"Error closing browser: {be}")
            return {"error": f"Playwright operation failed: {str(e)}"}

def extract_clinvar_data_lightweight(url):
    """
    Extract genetic variant data from a ClinVar page using requests and BeautifulSoup.
    Much lighter on resources than Playwright.
    """
    start_time = time.time()
    logger.info(f"Starting lightweight extraction for {url}")
    
    try:
        # Extract ClinVar ID from URL if present
        clinvar_id_match = re.search(r'/variation/(\d+)', url)
        clinvar_id = clinvar_id_match.group(1) if clinvar_id_match else None
        
        # If URL doesn't contain variation ID, try to use the full URL
        if not clinvar_id:
            extraction_url = url
        else:
            # Construct the ClinVar URL from the ID
            extraction_url = f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{clinvar_id}/?oq={clinvar_id}"
        
        # Use a reasonable timeout
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(extraction_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            logger.warning(f"Failed to load page. Status code: {response.status_code}")
            return {"error": f"Failed to load page. Status code: {response.status_code}"}
        
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Initialize data dictionary
        data = {
            "variant_summary": {},
            "submissions": [],
            "molecular_consequences": [],
            "identifiers": {},
            "gene_info": []
        }
        
        # Extract title and variant name
        data["variant_summary"]["title"] = soup.title.text if soup.title else "Unknown"
        
        h1 = soup.find('h1')
        if h1:
            data["variant_summary"]["variant_name"] = h1.text.strip()
        
        # Extract clinical significance if present
        clin_sig = soup.find(class_=lambda c: c and ('clinvar_review' in c or 'clinical_significance' in c))
        if clin_sig:
            data["variant_summary"]["clinical_significance"] = clin_sig.text.strip()
        
        # Extract tables
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    key = cells[0].text.strip().replace(":", "")
                    value = cells[1].text.strip()
                    if key and value:
                        data["identifiers"][key] = value
        
        # Extract gene information
        gene_links = soup.find_all('a', href=re.compile(r'/gene/'))
        for link in gene_links:
            gene_name = link.text.strip()
            if gene_name:
                gene_href = link.get('href')
                gene_id = gene_href.split('/')[-1].split('?')[0] if gene_href else None
                data["gene_info"].append({
                    "gene_name": gene_name,
                    "gene_id": gene_id
                })
        
        # Extract molecular consequences
        mol_tables = soup.find_all('table', id='hgvs-table')
        if not mol_tables:
            mol_tables = soup.find_all('table', {'class': re.compile(r'.*consequence.*')})
            
        for table in mol_tables:
            rows = table.find_all('tr')
            headers = []
            for row in rows:
                if row.find('th'):
                    headers = [th.text.strip() for th in row.find_all('th')]
                else:
                    cells = row.find_all('td')
                    if len(cells) >= 1:
                        entry = {}
                        for i, cell in enumerate(cells):
                            key = headers[i] if i < len(headers) else f"column_{i+1}"
                            entry[key] = cell.text.strip()
                        
                        if entry:
                            data["molecular_consequences"].append(entry)
        
        elapsed_time = time.time() - start_time
        logger.info(f"Lightweight extraction completed in {elapsed_time:.2f} seconds")
        return data
    
    except requests.RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        return {"error": f"Request failed: {str(e)}"}
    except Exception as e:
        logger.error(f"Lightweight extraction failed: {str(e)}")
        return {"error": f"Extraction failed: {str(e)}"}

def extract_clinvar_data_adaptive(url):
    """
    Adaptively choose the extraction method based on available resources.
    Falls back to lightweight method if Playwright fails.
    """
    try:
        # Check if we have sufficient resources for Playwright
        use_playwright = check_system_resources()
        
        if use_playwright:
            # Try Playwright first
            logger.info("Using Playwright extraction method")
            data = extract_clinvar_data_playwright(url)
            
            # If playwright fails, fall back to lightweight method
            if "error" in data:
                logger.warning(f"Playwright extraction failed: {data['error']}. Falling back to lightweight method.")
                data = extract_clinvar_data_lightweight(url)
        else:
            # Use lightweight method
            logger.info("Using lightweight extraction method")
            data = extract_clinvar_data_lightweight(url)
        
        return data
    
    except Exception as e:
        logger.error(f"Adaptive extraction failed: {str(e)}")
        return {"error": f"Extraction failed: {str(e)}"}

@app.route('/extract', methods=['GET', 'POST'])
def extract():
    try:
        # Extract URL from request
        url = None
        
        if request.method == 'POST':
            # Check if JSON data was sent
            if request.is_json:
                request_data = request.get_json()
                url = request_data.get('url')
                logger.info(f"Received POST request with JSON data. URL: {url}")
            else:
                # Otherwise, read from form data
                url = request.form.get('url')
                logger.info(f"Received POST request with form data. URL: {url}")
        else:  # GET request
            url = request.args.get('url')
            logger.info(f"Received GET request. URL: {url}")
        
        # Validate URL is provided
        if not url:
            logger.warning("URL parameter is missing")
            return jsonify({"error": "URL parameter is required"}), 400
        
        # Improved URL validation - accept any URL that contains clinvar
        # This is more permissive than the original validation
        if "clinvar" not in url.lower():
            # Try to be helpful by suggesting format
            logger.warning(f"Invalid URL format: {url}")
            return jsonify({
                "error": "URL must be a valid ClinVar variation page", 
                "example": "https://www.ncbi.nlm.nih.gov/clinvar/variation/12345/"
            }), 400
        
        # If URL doesn't have protocol, add it
        if not url.startswith('http'):
            url = 'https://' + url
            logger.info(f"Added protocol to URL: {url}")
        
        # Extract data from the URL
        logger.info(f"Starting extraction for URL: {url}")
        data = extract_clinvar_data_adaptive(url)
        
        logger.info("Extraction completed, returning response")
        return jsonify(data)
    
    except Exception as e:
        logger.error(f"API endpoint error: {str(e)}")
        return jsonify({"error": f"Request failed: {str(e)}"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    resources = {
        "memory_available_mb": round(psutil.virtual_memory().available / (1024 * 1024), 2),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "playwright_viable": check_system_resources()
    }
    return jsonify({
        "status": "healthy",
        "resources": resources,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route('/', methods=['GET'])
def home():
    """Render the home page template"""
    return render_template('index.html')

if __name__ == "__main__":
    # # Auto-open in browser if running directly
    # port = int(os.environ.get("PORT", 5000))
    # try:
    #     import webbrowser
    #     webbrowser.open(f"http://127.0.0.1:{port}")
    #     logger.info(f"Opening browser to http://127.0.0.1:{port}")
    # except Exception as e:
    #     logger.warning(f"Could not open browser: {e}")
    
    # logger.info(f"Starting server on port {port}")
    # serve(app, host="0.0.0.0", port=port, threads=4, connection_limit=200)

    import os
    from waitress import serve

    # Read port from environment; default to 5000 for local development
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting server on port {port}")

    serve(app, host="0.0.0.0", port=port, threads=4, connection_limit=200)
