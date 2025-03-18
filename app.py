from flask import Flask, request, render_template, jsonify
from playwright.sync_api import sync_playwright
from flask_cors import CORS 
from flask_cors import CORS# Add this import
import pandas as pd
import json
from datetime import datetime
import time
import re
from collections import defaultdict
from waitress import serve

import os
from dotenv import load_dotenv

print(f"Starting application with environment variables:")
print(f"PORT: {os.environ.get('PORT')}")
print(f"ENVIRONMENT: {os.environ.get('ENVIRONMENT')}")
# Add this near the top of your file, after imports
def initialize_playwright():
    import asyncio
    from playwright.sync_api import sync_playwright
    
    print("Installing Playwright browsers...")
    import subprocess
    try:
        # Install playwright browsers
        subprocess.run(["playwright", "install", "chromium"], check=True)
        print("Playwright browsers installed successfully")
    except Exception as e:
        print(f"Error installing Playwright browsers: {e}")
        # Continue anyway, as the browser might already be installed

# Call this function at the start of your app
initialize_playwright()

# Load environment variables
load_dotenv()

app = Flask(__name__)

# For development, you can allow all origins
if os.environ.get('ENVIRONMENT') == 'development':
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
else:
    # For production, specify allowed origins
    allowed_origins = os.environ.get('ALLOWED_ORIGINS', '').split(',')
    CORS(app, resources={r"/*": {"origins": allowed_origins}}, supports_credentials=True)
# # For development, you can allow all origins
# CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

# For production, specify all allowed origins
# CORS(app, resources={r"/*": {"origins": ["http://192.168.43.106:5000", "your-mobile-app-origin"]}})  # Specific app URL instead of wildcard *
  # For production, restrict to your app's domain


# Copy all the functions from your original script
def extract_clinvar_data(url):
    """
    Extract genetic variant data from a ClinVar page using Playwright.
    
    Args:
        url: URL of the ClinVar variant page
        
    Returns:
        Dictionary containing structured data from the ClinVar page
    """
    with sync_playwright() as p:
        # Launch browser with more options for reliability
        browser_args = []
        
        # Add production-specific configurations
        if os.environ.get('ENVIRONMENT') == 'production':
            browser_args = [
                '--disable-dev-shm-usage',
                '--disable-setuid-sandbox',
                '--no-sandbox',
                '--single-process',
                # This is crucial - tells Playwright not to use port 10000
                '--remote-debugging-port=0'  # Use a random port instead
            ]
        
        browser = p.chromium.launch(
            headless=True,
            args=browser_args  # Pass the browser arguments
        )
        
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        page = context.new_page()
        
        # More robust navigation and loading
        try:
            print(f"Navigating to {url}")
            response = page.goto(url, wait_until="networkidle", timeout=60000)
            
            if not response or response.status >= 400:
                print(f"Failed to load page properly. Status: {response.status if response else 'No response'}")
                return {"error": "Failed to load page"}
            
            # Wait for page content to be visible (more generic selector)
            print("Waiting for page to load completely...")
            page.wait_for_selector("body", state="visible", timeout=30000)
            
            # Additional wait to ensure JavaScript loads
            time.sleep(2)
            
            # Check if we're on the correct page by looking for variant header
            if not page.query_selector("h1") and not page.query_selector("#variant-header"):
                print("Warning: Page doesn't appear to be a ClinVar variant page. Proceeding anyway...")
        
        except Exception as e:
            print(f"Error during page loading: {e}")
            try:
                # Take a screenshot of what we got to diagnose issues
                page.screenshot(path="clinvar_loading_error.png")
                print("Saved screenshot to clinvar_loading_error.png")
                
                # Get the current content for debugging
                content = page.content()
                with open("clinvar_page_content.html", "w", encoding="utf-8") as f:
                    f.write(content)
                print("Saved current page content to clinvar_page_content.html")
                
                # Continue anyway, we might be able to extract some data
                print("Attempting to continue extraction despite loading issues...")
            except:
                print("Failed to save diagnostic information")
        
        # Initialize the data dictionary
        data = {
            "variant_summary": {},
            "submissions": [],
            "molecular_consequences": [],
            "protein_changes": {},
            "identifiers": {},
            "frequency_data": {},
            "gene_info": [],
            "conditions": [],
            "citations": []
        }
        
        # Extract variant summary information
        try:
            data["variant_summary"]["title"] = page.title()
            print(f"Page title: {data['variant_summary']['title']}")
            
            # Try to get the variant name from h1
            variant_header = page.query_selector("h1")
            if variant_header:
                data["variant_summary"]["variant_name"] = variant_header.inner_text().strip()
                print(f"Found variant name: {data['variant_summary']['variant_name']}")
        except Exception as e:
            print(f"Error extracting title information: {e}")
        
        # Extract variant details (more general approach)
        try:
            print("Attempting to extract variant details...")
            # Try different possible selectors for the variant details table
            details_section = None
            for selector in ["#variant-details", "table:has(th:has-text('Identifiers'))", "table:has(th:has-text('Type and length'))"]:
                details_section = page.query_selector(selector)
                if details_section:
                    print(f"Found variant details using selector: {selector}")
                    break
            
            if details_section:
                rows = details_section.query_selector_all("tr")
                if rows:
                    print(f"Found {len(rows)} rows in variant details table")
                    
                    for row in rows:
                        # Try to extract key-value pairs from the table rows
                        cells = row.query_selector_all("td, th")
                        if len(cells) >= 2:
                            key = cells[0].inner_text().strip().replace(":", "")
                            value = cells[1].inner_text().strip()
                            data["identifiers"][key] = value
                            print(f"Extracted detail: {key} = {value}")
            else:
                print("Could not find variant details section")
                
                # Attempt to extract any table data as fallback
                tables = page.query_selector_all("table")
                print(f"Found {len(tables)} tables on the page")
                
                for i, table in enumerate(tables):
                    print(f"Examining table {i+1}...")
                    rows = table.query_selector_all("tr")
                    if rows:
                        for row in rows:
                            cells = row.query_selector_all("td, th")
                            if len(cells) >= 2:
                                key = cells[0].inner_text().strip().replace(":", "")
                                value = cells[1].inner_text().strip()
                                if key and value and len(key) < 100:  # Reasonable length check
                                    data["identifiers"][key] = value
                                    print(f"Extracted from table {i+1}: {key} = {value}")
        except Exception as e:
            print(f"Error extracting variant details: {e}")
        
        # Extract any molecular data we can find
        try:
            print("Looking for molecular data...")
            # Try different selectors for molecular data tables
            mol_selectors = [
                "#hgvs-table",
                "table:has(th:has-text('Nucleotide'))",
                "table:has(th:has-text('Protein'))",
                "table:has(th:has-text('Molecular'))"
            ]
            
            for selector in mol_selectors:
                hgvs_table = page.query_selector(selector)
                if hgvs_table:
                    print(f"Found molecular data using selector: {selector}")
                    rows = hgvs_table.query_selector_all("tr")
                    
                    # Try to determine the column structure
                    header_row = hgvs_table.query_selector("tr:has(th)")
                    headers = []
                    if header_row:
                        header_cells = header_row.query_selector_all("th")
                        headers = [cell.inner_text().strip() for cell in header_cells]
                        print(f"Found headers: {headers}")
                    
                    # Process data rows
                    for row in rows:
                        if row.query_selector("th"):  # Skip header rows
                            continue
                            
                        cells = row.query_selector_all("td")
                        if len(cells) >= 2:
                            entry = {}
                            
                            # Map cells to headers if we have them, otherwise use generic keys
                            for i, cell in enumerate(cells):
                                key = headers[i] if i < len(headers) else f"column_{i+1}"
                                entry[key] = cell.inner_text().strip()
                            
                            data["molecular_consequences"].append(entry)
                            print(f"Added molecular data entry: {entry}")
        except Exception as e:
            print(f"Error extracting molecular data: {e}")
        
        # Frequency data - try different approaches
        try:
            print("Searching for frequency data...")
            freq_terms = [
                "Global minor allele",
                "Frequency",
                "GMAF",
                "Allele frequency",
                "gnomAD",
                "ExAC",
                "1000 Genomes"
            ]
            
            # First approach: look for rows with these terms
            for term in freq_terms:
                elements = page.query_selector_all(f"text/{term}")
                for el in elements:
                    try:
                        # Get parent row or containing element
                        row = el.evaluate("node => node.closest('tr') || node.parentElement")
                        if row:
                            # Try to get the term and value
                            text = page.evaluate("node => node.innerText", row)
                            parts = text.split(":")
                            if len(parts) >= 2:
                                key = parts[0].strip()
                                value = ":".join(parts[1:]).strip()
                                data["frequency_data"][key] = value
                                print(f"Found frequency data: {key} = {value}")
                    except Exception as inner_e:
                        print(f"Error processing frequency term '{term}': {inner_e}")
            
            # Second approach: look in tables for these terms
            tables = page.query_selector_all("table")
            for table in tables:
                rows = table.query_selector_all("tr")
                for row in rows:
                    text = row.inner_text().lower()
                    if any(term.lower() in text for term in freq_terms):
                        cells = row.query_selector_all("td")
                        if len(cells) >= 2:
                            key = cells[0].inner_text().strip()
                            value = cells[1].inner_text().strip()
                            data["frequency_data"][key] = value
                            print(f"Extracted frequency from table: {key} = {value}")
        except Exception as e:
            print(f"Error extracting frequency data: {e}")
        
        # Extract gene information
        try:
            print("Looking for gene information...")
            gene_selectors = ["#genes", "table:has(th:has-text('Gene'))", "section:has-text('Genes')"]
            
            for selector in gene_selectors:
                gene_section = page.query_selector(selector)
                if gene_section:
                    print(f"Found gene section with selector: {selector}")
                    gene_rows = gene_section.query_selector_all("tr")
                    
                    # Get headers
                    header_row = gene_section.query_selector("tr:has(th)")
                    headers = []
                    if header_row:
                        header_cells = header_row.query_selector_all("th")
                        headers = [cell.inner_text().strip() for cell in header_cells]
                        print(f"Gene table headers: {headers}")
                    
                    # Process data rows
                    for row in gene_rows:
                        if row.query_selector("th"):  # Skip header row
                            continue
                            
                        cells = row.query_selector_all("td")
                        if len(cells) >= 1:
                            gene_entry = {}
                            
                            # Map cells to headers if available
                            for i, cell in enumerate(cells):
                                key = headers[i] if i < len(headers) else f"column_{i+1}"
                                value = cell.inner_text().strip()
                                gene_entry[key] = value
                            
                            if gene_entry:
                                data["gene_info"].append(gene_entry)
                                print(f"Added gene entry: {gene_entry}")
                    
                    if data["gene_info"]:
                        break  # Stop if we found gene data
            
            # If we didn't find gene info in tables, look for mentions in text
            if not data["gene_info"]:
                gene_mentions = page.query_selector_all("a[href*='gene']")
                for mention in gene_mentions:
                    gene_name = mention.inner_text().strip()
                    if gene_name and len(gene_name) < 20:  # Reasonable length for gene name
                        data["gene_info"].append({"Gene": gene_name})
                        print(f"Found gene mention: {gene_name}")
        except Exception as e:
            print(f"Error extracting gene information: {e}")
        
        # Look for submission and condition information
        try:
            print("Searching for submissions and conditions...")
            
            # Check for sections with these terms
            sections = []
            for term in ["Submissions", "Condition", "submitter"]:
                sections.extend(page.query_selector_all(f"section:has-text('{term}')"))
                sections.extend(page.query_selector_all(f"div:has-text('{term}'):has(table)"))
            
            # Process each potential section
            for section in sections:
                section_text = section.inner_text().lower()
                table = section.query_selector("table")
                
                if table:
                    # Determine if this is submissions or conditions
                    target_list = "submissions" if "submitter" in section_text or "submission" in section_text else "conditions"
                    print(f"Processing table for {target_list}")
                    
                    # Get headers
                    headers = []
                    header_row = table.query_selector("tr:has(th)")
                    if header_row:
                        header_cells = header_row.query_selector_all("th")
                        headers = [cell.inner_text().strip() for cell in header_cells]
                    
                    # Process data rows
                    rows = table.query_selector_all("tr:not(:has(th))")
                    for row in rows:
                        cells = row.query_selector_all("td")
                        if len(cells) >= 1:
                            entry = {}
                            
                            # Map to headers or use column numbers
                            for i, cell in enumerate(cells):
                                key = headers[i] if i < len(headers) else f"column_{i+1}"
                                value = cell.inner_text().strip()
                                entry[key] = value
                            
                            # Add to appropriate list
                            if entry:
                                data[target_list].append(entry)
                                print(f"Added {target_list} entry: {entry}")
        except Exception as e:
            print(f"Error extracting submissions/conditions: {e}")
        
        # Look for last updated date
        try:
            print("Looking for last updated information...")
            date_patterns = [
                "Record last updated",
                "Last evaluated",
                "Last modified",
                "Last reviewed"
            ]
            
            for pattern in date_patterns:
                date_elements = page.query_selector_all(f"text/{pattern}")
                for el in date_elements:
                    try:
                        container = el.evaluate("node => node.parentElement")
                        if container:
                            full_text = page.evaluate("node => node.innerText", container)
                            # Extract date part
                            parts = full_text.split(":")
                            if len(parts) >= 2:
                                date_value = parts[1].strip()
                            else:
                                date_value = full_text.replace(pattern, "").strip()
                            
                            data["last_updated"] = date_value
                            print(f"Found last updated date: {date_value}")
                            break
                    except Exception as inner_e:
                        print(f"Error extracting date from '{pattern}': {inner_e}")
                
                if "last_updated" in data:
                    break
        except Exception as e:
            print(f"Error extracting last updated date: {e}")
        
        # Close browser
        browser.close()
        
        # Print summary of what we found
        print("\nExtraction Summary:")
        for key, value in data.items():
            if isinstance(value, dict):
                print(f"{key}: {len(value)} items")
            elif isinstance(value, list):
                print(f"{key}: {len(value)} items")
            else:
                print(f"{key}: {'Set' if value else 'Not found'}")
        
        return data

def format_molecular_consequences(consequences):
    """Format molecular consequences data into bullet points with proper annotations."""
    formatted = []
    for item in consequences:
        nucleotide = item.get("Nucleotide", "").replace("\nHelp", "").strip()
        consequence = item.get("Molecular\nconsequence", "").strip()
        
        # Create the formatted string
        if nucleotide:
            if consequence:
                formatted.append(f"• {nucleotide} – {consequence}")
            else:
                formatted.append(f"• {nucleotide} – (no annotation)")
    
    # Remove duplicates while preserving order
    unique_formatted = []
    seen = set()
    for item in formatted:
        if item not in seen:
            seen.add(item)
            unique_formatted.append(item)
            
    return unique_formatted

def extract_variant_type(data):
    """Extract variant type from submissions array."""
    for submission in data.get("submissions", []):
        # Check empty key for variant type
        if "" in submission and submission[""] and submission[""] != "":
            return submission[""]
    return "No data available"

def extract_condition(data):
    """Extract condition information from detailed submission entries."""
    for submission in data.get("submissions", []):
        if "Condition \nHelp" in submission and "Autosomal" in submission.get("Condition \nHelp", ""):
            # This is a detailed condition
            condition = submission.get("Condition \nHelp", "").replace("\n", "; ").strip()
            return condition
    
    # If no detailed condition found, try to find a simpler one
    for submission in data.get("submissions", []):
        if "Condition \nHelp" in submission and submission.get("Condition \nHelp", "").strip():
            return submission.get("Condition \nHelp", "").strip()
    
    return "No data available"

def extract_classification(data):
    """Extract classification from detailed submission entries."""
    classification_info = []
    
    # First, try to get the simple classification with submission count
    for submission in data.get("submissions", []):
        if "Classification \nHelp\n\n(# of submissions)" in submission:
            classification = submission.get("Classification \nHelp\n\n(# of submissions)", "").strip()
            if classification:
                classification_info.append(classification)
                break
    
    # Then get the detailed classification information
    detailed_info = []
    for submission in data.get("submissions", []):
        if "Classification \nHelp\n\n(Last evaluated)" in submission and "Review status \nHelp\n\n(Assertion criteria)" in submission:
            classification = submission.get("Classification \nHelp\n\n(Last evaluated)", "").strip()
            review_status = submission.get("Review status \nHelp\n\n(Assertion criteria)", "").strip()
            
            if classification or review_status:
                # Extract date
                date_match = re.search(r'\((.*?\d{4})\)', classification)
                date = date_match.group(1) if date_match else ""
                
                # Extract contributing status
                contributing = "Contributing to aggregate classification" if "Contributing to aggregate classification" in classification else ""
                
                # Extract method details
                method_info = []
                if "ACMG Guidelines" in review_status:
                    method_info.append("ACMG Guidelines, 2015")
                if "Method:" in review_status:
                    method_match = re.search(r'Method:\s*(\w+)', review_status)
                    if method_match:
                        method_info.append(f"Method: {method_match.group(1)}")
                
                # Combine all detailed information
                details = []
                if date:
                    details.append(date)
                if contributing:
                    details.append(contributing)
                if method_info:
                    details.append("; ".join(method_info))
                
                if details:
                    detailed_info.append("; ".join(details))
                break
    
    # Combine classification with detailed information
    if classification_info and detailed_info:
        return f"{classification_info[0]} ({'; '.join(detailed_info)})"
    elif classification_info:
        return classification_info[0]
    elif detailed_info:
        return f"Unknown ({'; '.join(detailed_info)})"
    else:
        return "No data available"

def extract_gene_name(data):
    """Extract gene name from gene_info array."""
    for gene in data.get("gene_info", []):
        if "Gene" in gene and gene.get("Gene", "").strip():
            return gene.get("Gene", "").strip()
    return "No data available"

def extract_molecular_consequences(data):
    """Extract molecular consequences from molecular_consequences array."""
    if data.get("molecular_consequences", []):
        return format_molecular_consequences(data.get("molecular_consequences", []))
    return ["• No data available"]

def format_clinvar_data(data):
    """Format ClinVar data according to the specified format."""
    variant_type = extract_variant_type(data)
    condition = extract_condition(data)
    classification = extract_classification(data)
    gene_name = extract_gene_name(data)
    molecular_consequences = extract_molecular_consequences(data)
    
    # Format the output
    output = []
    output.append("**Variant Type**")
    output.append(f"• {variant_type}")
    output.append("**Condition**")
    output.append(f"• {condition}")
    output.append("**Classification**")
    output.append(f"• {classification}")
    output.append("**Gene Name**")
    output.append(f"• {gene_name}")
    output.append("**Molecular Consequences**")
    output.extend(molecular_consequences)
    
    return "\n".join(output)

# Create a route for the main page with a form
@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    loading = False
    error = None
    
    if request.method == 'POST':
        clinvar_id = request.form.get('url')
        
        if not clinvar_id:
            error = "Please enter a ClinVar/Allele ID"
        else:
            try:
                # Construct the ClinVar URL from the ID
                url = f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{clinvar_id}/?oq={clinvar_id}"
                loading = True
                # Extract data from URL
                clinvar_data = extract_clinvar_data(url)
                
                # Format the data
                result = format_clinvar_data(clinvar_data)
                loading = False
            except Exception as e:
                error = f"An error occurred: {str(e)}"
                loading = False
    
    # Render the template with the form and any results
    return render_template('index.html', result=result, loading=loading, error=error)

@app.route('/ClinVarExtractor', methods=['POST'])
def clinvar_extractor():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
            
        clinvar_id = data.get('url')
        if not clinvar_id:
            return jsonify({"error": "ClinVar/Allele ID is required"}), 400
        
        # Construct the ClinVar URL from the ID
        url = f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{clinvar_id}/?oq={clinvar_id}"
        
        # Extract data from URL
        clinvar_data = extract_clinvar_data(url)
        
        # Format the data
        formatted_result = format_clinvar_data(clinvar_data)
        
        # Return both raw and formatted data
        return jsonify({
            "raw_data": clinvar_data,
            "formatted_data": formatted_result
        })
    except Exception as e:
        # Make sure we return JSON even for errors
        return jsonify({"error": str(e)}), 500

# Create a JSON API endpoint for programmatic access
@app.route('/api/extract', methods=['POST'])
def api_extract():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
            
        clinvar_id = data.get('url')
        if not clinvar_id:
            return jsonify({"error": "ClinVar/Allele ID is required"}), 400
        
        # Construct the ClinVar URL from the ID
        url = f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{clinvar_id}/?oq={clinvar_id}"
        
        # Extract data from URL
        clinvar_data = extract_clinvar_data(url)
        
        # Format the data
        formatted_result = format_clinvar_data(clinvar_data)
        
        # Return both raw and formatted data
        return jsonify({
            "raw_data": clinvar_data,
            "formatted_data": formatted_result
        })
    except Exception as e:
        # Make sure we return JSON even for errors
        return jsonify({"error": str(e)}), 500

# Template for the HTML interface
@app.route('/templates/index.html')
def get_template():
    return render_template('index.html')

@app.route('/api/test', methods=['GET'])
def api_test():
    return jsonify({"status": "success", "message": "API is working properly"})

# Create the templates directory and index.html file
import os

def create_templates():
    # Create the templates directory if it doesn't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create the index.html file
    with open('templates/index.html', 'w') as f:
        f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>ClinVar Data Extractor</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
            color: #333;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            background-color: #f9f9f9;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
        }
        h1 {
            color: #2c3e50;
            text-align: center;
        }
        form {
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input[type="text"] {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 16px;
        }
        button {
            background-color: #3498db;
            color: white;
            border: none;
            padding: 10px 20px;
            font-size: 16px;
            border-radius: 4px;
            cursor: pointer;
        }
        button:hover {
            background-color: #2980b9;
        }
        .result {
            margin-top: 20px;
            padding: 15px;
            background-color: white;
            border-radius: 4px;
            border-left: 4px solid #3498db;
        }
        .error {
            color: #c0392b;
            padding: 10px;
            background-color: #fadbd8;
            border-radius: 4px;
            margin-bottom: 15px;
        }
        .loading {
            text-align: center;
            padding: 20px;
        }
        pre {
            white-space: pre-wrap;
            word-wrap: break-word;
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 4px;
            line-height: 1.4;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>ClinVar Data Extractor</h1>
        
        <form method="POST" action="/">
            <div class="form-group">
                <label for="url">Enter a valid ClinVar/Allele ID:</label>
                <input type="text" id="url" name="url" placeholder="Example: 1110866" required>
        </div>
            <button type="submit">Extract Data</button>
        </form>
        
        {% if error %}
        <div class="error">
            {{ error }}
        </div>
        {% endif %}
        
        {% if loading %}
        <div class="loading">
            <p>Loading data... This may take a moment.</p>
        </div>
        {% endif %}
        
        {% if result %}
        <div class="result">
            <h2>Results:</h2>
            <pre>{{ result }}</pre>
        </div>
        {% endif %}
    </div>
</body>
</html>
        """)
        
# Add this right after the POST endpoint above
@app.route('/ClinVarExtractor', methods=['GET'])
def clinvar_extractor_get():
    # Even for GET requests, return JSON, not HTML
    return jsonify({"error": "This endpoint requires a POST request with JSON data"}), 405

# Create the templates when starting the app
create_templates()

# if __name__ == "__main__":
#     # Run the flask app
#     # app.run(debug=True, host='0.0.0.0', port=5000)
#     from waitress import serve
#     serve(app, host='0.0.0.0', port=5000)
# Modify the bottom of your file
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting application on port {port}")
    if os.environ.get('ENVIRONMENT') == 'development':
        app.run(debug=True, host='0.0.0.0', port=port)
    else:
        print(f"Starting waitress server on port {port}")
        serve(app, host='0.0.0.0', port=port)
