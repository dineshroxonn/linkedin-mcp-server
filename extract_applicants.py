#!/usr/bin/env python3
"""
Standalone script to extract all job applicants with their contact info.
Run this directly in terminal to avoid Claude Desktop timeout issues.

Usage:
    cd /Users/dineshrampalli/Desktop/linkedin-mcp-server
    uv run python extract_applicants.py
"""

import asyncio
import csv
import json
import logging
import sys
import time
from datetime import datetime

# Enable unbuffered output
sys.stdout = sys.stdout if hasattr(sys.stdout, 'reconfigure') else sys.stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)

# Enable logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# Import the tool
from linkedin_mcp_server.tools.recruiter import register_recruiter_tools
from fastmcp import FastMCP


async def extract_all_applicants(job_id: str, max_applicants: int = 1200):
    """Extract all applicants and save to CSV."""

    print(f"\n{'='*60}")
    print(f"LinkedIn Applicant Extractor")
    print(f"{'='*60}")
    print(f"Job ID: {job_id}")
    print(f"Max applicants: {max_applicants}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # Create MCP and register tools
    mcp = FastMCP('extractor')
    register_recruiter_tools(mcp)

    # Get the tool
    tool = mcp._tool_manager._tools.get('get_applicants_with_contact')

    if not tool:
        print("ERROR: Could not find get_applicants_with_contact tool")
        return

    print("Starting extraction...\n")

    # Run the extraction
    result = await tool.fn(
        job_id=job_id,
        max_applicants=max_applicants,
        delay_seconds=0.2  # Fast extraction
    )

    if "error" in result:
        print(f"ERROR: {result.get('message', 'Unknown error')}")
        return

    applicants = result.get("applicants", [])
    total = result.get("total_processed", 0)
    emails_found = result.get("emails_found", 0)
    phones_found = result.get("phones_found", 0)

    print(f"\n{'='*60}")
    print(f"EXTRACTION COMPLETE")
    print(f"{'='*60}")
    print(f"Total processed: {total}")
    print(f"Emails found: {emails_found}")
    print(f"Phones found: {phones_found}")
    print(f"{'='*60}\n")

    # Save to CSV
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_filename = f"applicants_{job_id}_{timestamp}.csv"

    with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['name', 'email', 'phone', 'profile_url', 'headline', 'location'])
        writer.writeheader()
        for app in applicants:
            writer.writerow({
                'name': app.get('name', ''),
                'email': app.get('email', ''),
                'phone': app.get('phone', ''),
                'profile_url': app.get('profile_url', ''),
                'headline': app.get('headline', ''),
                'location': app.get('location', '')
            })

    print(f"Saved to: {csv_filename}")

    # Also save as JSON
    json_filename = f"applicants_{job_id}_{timestamp}.json"
    with open(json_filename, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Saved to: {json_filename}")

    # Print first 10 applicants as preview
    print(f"\n{'='*60}")
    print("PREVIEW (first 10 applicants):")
    print(f"{'='*60}")
    for i, app in enumerate(applicants[:10]):
        print(f"{i+1}. {app.get('name', 'N/A')}")
        print(f"   Email: {app.get('email', 'N/A')}")
        print(f"   Phone: {app.get('phone', 'N/A')}")
        print(f"   Profile: {app.get('profile_url', 'N/A')}")
        print()

    if len(applicants) > 10:
        print(f"... and {len(applicants) - 10} more applicants")

    return result


if __name__ == "__main__":
    # Configuration
    JOB_ID = "4325022456"  # Your job ID
    MAX_APPLICANTS = 1200  # Set high to get all ~1100 applicants

    # Run the extraction
    asyncio.run(extract_all_applicants(JOB_ID, MAX_APPLICANTS))
