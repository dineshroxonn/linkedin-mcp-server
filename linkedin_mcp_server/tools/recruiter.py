# linkedin_mcp_server/tools/recruiter.py
"""
LinkedIn recruiter/hiring manager tools for applicant management.

Provides MCP tools for accessing job applicants, extracting contact information,
and managing recruitment workflows with proper error handling.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from linkedin_mcp_server.error_handler import handle_tool_error, safe_get_driver

logger = logging.getLogger(__name__)


def register_recruiter_tools(mcp: FastMCP) -> None:
    """
    Register all recruiter-related tools with the MCP server.

    Args:
        mcp (FastMCP): The MCP server instance
    """

    @mcp.tool()
    async def get_job_applicants(job_id: str, max_applicants: int = 50) -> Dict[str, Any]:
        """
        Get list of applicants for a job posting you manage.

        IMPORTANT: This requires LinkedIn Recruiter or Hiring Manager access to the job.
        You must be logged in with an account that has permission to view applicants.

        Args:
            job_id (str): LinkedIn job ID (e.g., "4325022456")
            max_applicants (int): Maximum number of applicants to fetch (default: 50)

        Returns:
            Dict[str, Any]: Contains job_id, total_found, and list of applicants
        """
        try:
            driver = safe_get_driver()
            applicants = []
            seen_names = set()

            # Navigate to the hiring applicants page
            url = f"https://www.linkedin.com/hiring/applicants/?jobId={job_id}"
            logger.info(f"Navigating to: {url}")
            driver.get(url)
            time.sleep(5)

            # Check if we landed on the right page
            current_url = driver.current_url
            if "hiring" not in current_url and "applicants" not in current_url:
                return {
                    "error": "access_denied",
                    "message": "Could not access applicant list. Make sure you have Recruiter/Hiring Manager access to this job.",
                    "job_id": job_id,
                    "current_url": current_url
                }

            # Extract applicants using aria-label with "Verified profile"
            def extract_current_applicants():
                """Extract new applicants, handling stale element references."""
                new_applicants = []
                try:
                    name_elements = driver.find_elements(
                        By.XPATH,
                        '//*[contains(@aria-label, ", Verified profile")]'
                    )
                    for elem in name_elements:
                        try:
                            aria = elem.get_attribute('aria-label')
                            if aria and ', Verified profile' in aria:
                                name = aria.replace(', Verified profile', '').strip()
                                if name and name not in seen_names:
                                    seen_names.add(name)
                                    new_applicants.append({
                                        'name': name,
                                        'profile_url': None,
                                        'headline': None,
                                        'location': None
                                    })
                        except StaleElementReferenceException:
                            continue  # Element became stale, skip it
                except Exception as e:
                    logger.debug(f"Error extracting applicants: {e}")
                return new_applicants

            def find_and_click_load_more():
                """Find Load more button by innerHTML and click it."""
                try:
                    buttons = driver.find_elements(By.TAG_NAME, 'button')
                    for btn in buttons:
                        try:
                            inner_html = btn.get_attribute('innerHTML')
                            if inner_html and 'Load more' in inner_html:
                                if btn.is_displayed() and btn.is_enabled():
                                    # Scroll and click in one JS call to minimize stale element issues
                                    driver.execute_script('''
                                        arguments[0].scrollIntoView({block: "center"});
                                        arguments[0].click();
                                    ''', btn)
                                    return True
                        except StaleElementReferenceException:
                            continue
                    return False
                except Exception as e:
                    logger.debug(f"Error finding Load more button: {e}")
                    return False

            # Get initial applicants
            initial = extract_current_applicants()
            applicants.extend(initial)
            logger.info(f"Found {len(initial)} initial applicants")

            # Click "Load more" button repeatedly to load all applicants
            load_more_clicks = 0
            max_clicks = (max_applicants // 5) + 10  # More generous estimate
            consecutive_no_new = 0

            while len(applicants) < max_applicants and load_more_clicks < max_clicks:
                if find_and_click_load_more():
                    load_more_clicks += 1
                    logger.info(f"Clicked Load more (click {load_more_clicks})")

                    time.sleep(2)  # Wait for new applicants to load

                    # Extract any new applicants
                    new_applicants = extract_current_applicants()
                    applicants.extend(new_applicants)

                    if not new_applicants:
                        consecutive_no_new += 1
                        if consecutive_no_new >= 5:
                            logger.info("No new applicants found after 5 consecutive clicks")
                            break
                    else:
                        consecutive_no_new = 0
                        logger.info(f"Found {len(new_applicants)} new applicants, total: {len(applicants)}")
                else:
                    logger.info("No more 'Load more' button found - reached end of list")
                    break

            # Trim to max_applicants
            applicants = applicants[:max_applicants]

            return {
                "job_id": job_id,
                "total_found": len(applicants),
                "applicants": applicants
            }

        except Exception as e:
            return handle_tool_error(e, "get_job_applicants")

    @mcp.tool()
    async def get_applicant_contact_info(profile_url: str) -> Dict[str, Any]:
        """
        Get contact information (email, phone) for a LinkedIn profile.

        This clicks the "Contact info" button on a profile to extract email/phone
        if the user has made them visible.

        Args:
            profile_url (str): Full LinkedIn profile URL (e.g., "https://www.linkedin.com/in/username/")

        Returns:
            Dict[str, Any]: Contact information including email and phone if available
        """
        try:
            driver = safe_get_driver()

            # Navigate to profile
            logger.info(f"Navigating to profile: {profile_url}")
            driver.get(profile_url)
            time.sleep(3)

            contact_info = {
                "profile_url": profile_url,
                "email": None,
                "phone": None,
                "websites": [],
                "twitter": None,
                "birthday": None,
                "connected": None
            }

            # Try to find and click the "Contact info" link/button
            contact_button_selectors = [
                (By.XPATH, "//a[contains(@href, 'overlay/contact-info')]"),
                (By.XPATH, "//span[text()='Contact info']/ancestor::a"),
                (By.XPATH, "//button[contains(text(), 'Contact info')]"),
                (By.CSS_SELECTOR, "a[href*='contact-info']"),
                (By.ID, "top-card-text-details-contact-info"),
            ]

            contact_button = None
            for selector_type, selector_value in contact_button_selectors:
                try:
                    contact_button = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((selector_type, selector_value))
                    )
                    if contact_button:
                        break
                except (TimeoutException, NoSuchElementException):
                    continue

            if not contact_button:
                return {
                    **contact_info,
                    "error": "contact_button_not_found",
                    "message": "Could not find Contact info button. User may not have shared contact details."
                }

            # Click to open contact info modal
            try:
                contact_button.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", contact_button)

            time.sleep(2)

            # Extract contact info from modal
            # Email
            try:
                email_elem = driver.find_element(
                    By.XPATH,
                    "//section[contains(@class, 'ci-email')]//a | //a[contains(@href, 'mailto:')]"
                )
                contact_info["email"] = email_elem.text or email_elem.get_attribute("href").replace("mailto:", "")
            except NoSuchElementException:
                pass

            # Phone
            try:
                phone_elem = driver.find_element(
                    By.XPATH,
                    "//section[contains(@class, 'ci-phone')]//span | //a[contains(@href, 'tel:')]"
                )
                contact_info["phone"] = phone_elem.text or phone_elem.get_attribute("href").replace("tel:", "")
            except NoSuchElementException:
                pass

            # Websites
            try:
                website_elems = driver.find_elements(
                    By.XPATH,
                    "//section[contains(@class, 'ci-websites')]//a"
                )
                contact_info["websites"] = [elem.get_attribute("href") for elem in website_elems]
            except NoSuchElementException:
                pass

            # Twitter
            try:
                twitter_elem = driver.find_element(
                    By.XPATH,
                    "//section[contains(@class, 'ci-twitter')]//a"
                )
                contact_info["twitter"] = twitter_elem.text or twitter_elem.get_attribute("href")
            except NoSuchElementException:
                pass

            # Close modal
            try:
                close_button = driver.find_element(
                    By.XPATH,
                    "//button[@aria-label='Dismiss' or contains(@class, 'artdeco-modal__dismiss')]"
                )
                close_button.click()
            except NoSuchElementException:
                # Press escape to close
                from selenium.webdriver.common.keys import Keys
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)

            return contact_info

        except Exception as e:
            return handle_tool_error(e, "get_applicant_contact_info")

    @mcp.tool()
    async def bulk_get_applicant_emails(
        job_id: str,
        max_applicants: int = 50,
        delay_seconds: float = 2.0
    ) -> Dict[str, Any]:
        """
        Get email addresses for all applicants of a job posting.

        This combines get_job_applicants and get_applicant_contact_info to
        extract emails for multiple applicants. Use with caution to avoid
        rate limiting.

        Args:
            job_id (str): LinkedIn job ID
            max_applicants (int): Maximum applicants to process (default: 50)
            delay_seconds (float): Delay between profile visits to avoid rate limiting (default: 2.0)

        Returns:
            Dict[str, Any]: List of applicants with their contact info
        """
        try:
            # First get the applicant list
            applicants_result = await get_job_applicants(job_id, max_applicants)

            if "error" in applicants_result:
                return applicants_result

            applicants = applicants_result.get("applicants", [])
            results = []

            for i, applicant in enumerate(applicants):
                profile_url = applicant.get("profile_url")
                if not profile_url:
                    results.append({**applicant, "contact_info": None, "error": "no_profile_url"})
                    continue

                logger.info(f"Processing applicant {i+1}/{len(applicants)}: {profile_url}")

                # Get contact info
                contact_info = await get_applicant_contact_info(profile_url)

                results.append({
                    **applicant,
                    "contact_info": contact_info
                })

                # Delay to avoid rate limiting
                if i < len(applicants) - 1:
                    time.sleep(delay_seconds)

            # Summary
            emails_found = sum(1 for r in results if r.get("contact_info", {}).get("email"))

            return {
                "job_id": job_id,
                "total_processed": len(results),
                "emails_found": emails_found,
                "applicants": results
            }

        except Exception as e:
            return handle_tool_error(e, "bulk_get_applicant_emails")

    @mcp.tool()
    async def get_applicants_with_contact(
        job_id: str,
        max_applicants: int = 20,
        delay_seconds: float = 0.5,
        rating_filter: str = "GOOD_FIT"
    ) -> Dict[str, Any]:
        """
        Get applicants with their contact info directly from the LinkedIn hiring page.

        This tool iterates through applicants on the hiring page and clicks the
        "Contact" button for each one to extract phone numbers. This is more
        efficient than visiting each profile separately.

        Args:
            job_id (str): LinkedIn job ID (e.g., "4325022456")
            max_applicants (int): Maximum number of applicants to process (default: 50)
            delay_seconds (float): Delay between processing each applicant (default: 1.5)
            rating_filter (str): Filter by rating - "GOOD_FIT" (Top fit), "MAYBE", "NOT_A_FIT", or "ALL" for no filter

        Returns:
            Dict[str, Any]: Contains job_id, total_found, and list of applicants with contact info
        """
        from selenium.webdriver.common.keys import Keys

        try:
            driver = safe_get_driver()
            applicants_with_contact = []
            processed_names = set()

            # Navigate to the hiring applicants page with rating filter
            if rating_filter and rating_filter.upper() != "ALL":
                url = f"https://www.linkedin.com/hiring/applicants/?jobId={job_id}&rating={rating_filter.upper()}"
            else:
                url = f"https://www.linkedin.com/hiring/applicants/?jobId={job_id}"
            logger.info(f"Navigating to: {url}")
            driver.get(url)
            time.sleep(4)  # Wait for page load

            # Check if we landed on the right page
            current_url = driver.current_url
            print(f"Current URL: {current_url}", flush=True)
            if "hiring" not in current_url and "applicants" not in current_url:
                return {
                    "error": "access_denied",
                    "message": "Could not access applicant list. Make sure you have Recruiter/Hiring Manager access.",
                    "job_id": job_id,
                    "current_url": current_url
                }

            # LinkedIn shows applicants with rating=GOOD_FIT (Top fit) filter
            # This is fine - Top fit has ~1,173 applicants which is what we want
            time.sleep(1)
            current_url = driver.current_url
            print(f"Current URL: {current_url}", flush=True)

            import re

            # Check initial applicant count
            initial_check_cards = driver.find_elements(By.XPATH, '//*[contains(@aria-label, ", Verified profile")]')
            initial_count = len(initial_check_cards)
            print(f"Initial applicant count visible: {initial_count}", flush=True)

            def get_applicant_cards():
                """Get all clickable applicant name elements."""
                try:
                    # Find elements with "Verified profile" in aria-label - these are clickable
                    return driver.find_elements(
                        By.XPATH,
                        '//*[contains(@aria-label, ", Verified profile")]'
                    )
                except Exception:
                    return []

            def click_contact_and_extract():
                """Click Contact button and extract phone/email from popup."""
                contact_info = {"phone": None, "email": None}

                try:
                    # Look for Contact button in the right panel
                    contact_btn_selectors = [
                        '//button[contains(., "Contact")]',
                        '//button[@aria-label="Contact"]',
                        '//button[.//span[contains(text(), "Contact")]]',
                    ]

                    contact_btn = None
                    for selector in contact_btn_selectors:
                        try:
                            btns = driver.find_elements(By.XPATH, selector)
                            for btn in btns:
                                if btn.is_displayed() and btn.is_enabled():
                                    contact_btn = btn
                                    break
                            if contact_btn:
                                break
                        except Exception:
                            continue

                    if not contact_btn:
                        return contact_info

                    # Click the Contact button
                    driver.execute_script("arguments[0].click();", contact_btn)
                    time.sleep(0.4)  # Reduced from 1s

                    # Extract phone number from popup
                    phone_selectors = [
                        '//a[contains(@href, "tel:")]',
                        '//*[contains(@class, "phone")]//span',
                        '//div[contains(@class, "contact")]//a[contains(@href, "tel:")]',
                    ]
                    for selector in phone_selectors:
                        try:
                            phone_elem = driver.find_element(By.XPATH, selector)
                            phone_val = phone_elem.get_attribute("href")
                            if phone_val and "tel:" in phone_val:
                                contact_info["phone"] = phone_val.replace("tel:", "")
                                break
                            elif phone_elem.text:
                                contact_info["phone"] = phone_elem.text.strip()
                                break
                        except NoSuchElementException:
                            continue

                    # Extract email if visible (not a link that opens new page)
                    email_selectors = [
                        '//a[contains(@href, "mailto:")]',
                        '//*[contains(@class, "email")]//span',
                    ]
                    for selector in email_selectors:
                        try:
                            email_elem = driver.find_element(By.XPATH, selector)
                            email_val = email_elem.get_attribute("href")
                            if email_val and "mailto:" in email_val:
                                contact_info["email"] = email_val.replace("mailto:", "")
                                break
                            elif email_elem.text and "@" in email_elem.text:
                                contact_info["email"] = email_elem.text.strip()
                                break
                        except NoSuchElementException:
                            continue

                    # Close the popup by pressing Escape
                    try:
                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                        time.sleep(0.15)  # Minimal wait for popup to close
                    except Exception:
                        pass

                except Exception as e:
                    logger.debug(f"Error extracting contact: {e}")

                return contact_info

            def extract_applicant_details():
                """Extract name, headline, location from currently selected applicant."""
                details = {"name": None, "headline": None, "location": None, "profile_url": None}

                try:
                    # Name from the right panel header
                    name_selectors = [
                        '//h1[contains(@class, "hiring")]',
                        '//div[contains(@class, "profile")]//h1',
                        '//*[contains(@class, "applicant-name")]',
                    ]
                    for selector in name_selectors:
                        try:
                            elem = driver.find_element(By.XPATH, selector)
                            if elem.text:
                                details["name"] = elem.text.strip()
                                break
                        except NoSuchElementException:
                            continue

                    # Headline/title
                    headline_selectors = [
                        '//div[contains(@class, "headline")]',
                        '//p[contains(@class, "subtitle")]',
                    ]
                    for selector in headline_selectors:
                        try:
                            elem = driver.find_element(By.XPATH, selector)
                            if elem.text:
                                details["headline"] = elem.text.strip()
                                break
                        except NoSuchElementException:
                            continue

                    # Profile URL - look for "View full profile" link
                    try:
                        profile_link = driver.find_element(
                            By.XPATH,
                            '//a[contains(@aria-label, "View full profile") or contains(@href, "/in/")]'
                        )
                        href = profile_link.get_attribute("href")
                        if href and "/in/" in href:
                            details["profile_url"] = href.split("?")[0]
                    except NoSuchElementException:
                        pass

                except Exception as e:
                    logger.debug(f"Error extracting details: {e}")

                return details

            def find_and_click_load_more():
                """Find Load more button by multiple methods and click it."""
                try:
                    # Method 1: Find by innerHTML containing "Load more"
                    buttons = driver.find_elements(By.TAG_NAME, 'button')
                    for btn in buttons:
                        try:
                            inner_html = btn.get_attribute('innerHTML')
                            if inner_html and 'Load more' in inner_html:
                                if btn.is_displayed() and btn.is_enabled():
                                    driver.execute_script('''
                                        arguments[0].scrollIntoView({block: "center"});
                                        arguments[0].click();
                                    ''', btn)
                                    return True
                        except StaleElementReferenceException:
                            continue

                    # Method 2: Find by text content "Load more applicants"
                    load_more_selectors = [
                        '//button[contains(text(), "Load more")]',
                        '//button[contains(., "Load more applicants")]',
                        '//button[contains(@class, "load-more")]',
                        '//div[contains(@class, "load-more")]//button',
                    ]
                    for selector in load_more_selectors:
                        try:
                            elems = driver.find_elements(By.XPATH, selector)
                            for elem in elems:
                                if elem.is_displayed() and elem.is_enabled():
                                    driver.execute_script('''
                                        arguments[0].scrollIntoView({block: "center"});
                                        arguments[0].click();
                                    ''', elem)
                                    return True
                        except Exception:
                            continue

                    return False
                except Exception:
                    return False

            def scroll_applicant_list():
                """Scroll the applicant list container to trigger lazy loading."""
                try:
                    # Find the scrollable container for the applicant list
                    scroll_containers = driver.find_elements(
                        By.XPATH,
                        '//div[contains(@class, "applicant-list") or contains(@class, "hiring-applicants")]//div[contains(@style, "overflow")]'
                    )
                    if scroll_containers:
                        driver.execute_script(
                            "arguments[0].scrollTop = arguments[0].scrollHeight",
                            scroll_containers[0]
                        )
                        return True

                    # Alternative: scroll the main list by scrolling the last visible card into view
                    cards = get_applicant_cards()
                    if cards:
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block: 'end', behavior: 'smooth'});",
                            cards[-1]
                        )
                        return True
                except Exception:
                    pass
                return False

            # First, check initial applicant count to ensure page loaded
            initial_cards = get_applicant_cards()
            print(f"Initial applicants visible: {len(initial_cards)}", flush=True)

            # If no applicants visible after initial wait, wait more and retry
            if len(initial_cards) == 0:
                print("No applicants visible yet, waiting longer for page to load...", flush=True)
                time.sleep(5)
                initial_cards = get_applicant_cards()
                print(f"After extended wait: {len(initial_cards)} applicants visible", flush=True)

            if len(initial_cards) == 0:
                return {
                    "error": "no_applicants_found",
                    "message": "No applicants found. Page may not have loaded properly or job has no applicants.",
                    "job_id": job_id,
                    "current_url": driver.current_url
                }

            # Load ALL applicants by clicking "Load more" repeatedly and scrolling
            print("Loading all applicants (this may take a while for 1000+ applicants)...", flush=True)
            load_more_clicks = 0
            max_load_clicks = 500  # Allow up to 500 clicks (enough for ~2500 applicants)
            consecutive_failures = 0
            last_count = len(initial_cards)

            while load_more_clicks < max_load_clicks and consecutive_failures < 10:
                # Try clicking Load more button
                if find_and_click_load_more():
                    load_more_clicks += 1
                    consecutive_failures = 0
                    if load_more_clicks % 10 == 0:
                        current_cards = get_applicant_cards()
                        current_count = len(current_cards)
                        print(f"Load more #{load_more_clicks}: {current_count} applicants loaded (+{current_count - last_count})", flush=True)
                        last_count = current_count
                    time.sleep(0.5)  # Reduced from 1.5s
                else:
                    # No Load more button - try scrolling to trigger lazy load
                    scroll_applicant_list()
                    time.sleep(0.5)  # Reduced from 1.5s

                    # Check if we got more applicants after scrolling
                    new_cards = get_applicant_cards()
                    if len(new_cards) > last_count:
                        print(f"Scroll loaded more: {len(new_cards)} applicants (+{len(new_cards) - last_count})", flush=True)
                        last_count = len(new_cards)
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures % 3 == 0:
                            print(f"No new applicants loaded (attempt {consecutive_failures}/10)...", flush=True)
                        time.sleep(0.3)  # Reduced from 1s

            final_cards = get_applicant_cards()
            total_cards = len(final_cards)
            print(f"Finished loading - clicked Load more {load_more_clicks} times, {total_cards} total applicants to process", flush=True)

            # LinkedIn uses virtual scrolling - cards not in viewport are removed from DOM
            # Strategy: scroll back to top, then process by scrolling to each card

            # Scroll applicant list back to top
            def scroll_list_to_top():
                """Scroll the applicant list container back to top."""
                try:
                    # Find the scrollable container
                    containers = driver.find_elements(By.XPATH, '//div[contains(@class, "hiring-applicants__list")]')
                    if containers:
                        driver.execute_script("arguments[0].scrollTop = 0;", containers[0])
                        time.sleep(0.3)
                        return True
                    # Try alternative method
                    driver.execute_script("""
                        const lists = document.querySelectorAll('[class*="applicants__list"], [class*="hiring-applicant"]');
                        for (const list of lists) {
                            if (list.scrollHeight > list.clientHeight) {
                                list.scrollTop = 0;
                                return;
                            }
                        }
                    """)
                    time.sleep(0.3)
                    return True
                except Exception:
                    return False

            print("Scrolling list back to top before processing...", flush=True)
            scroll_list_to_top()
            time.sleep(0.5)

            # Now process all loaded applicants
            # LinkedIn uses virtual DOM - we need to scroll incrementally to reveal cards
            total_processed = 0
            scroll_position = 0
            scroll_increment = 200  # Scroll by 200px each time to reveal new cards
            consecutive_no_progress = 0
            max_no_progress = 50  # Give up after 50 scroll attempts with no new processed

            def scroll_list_by_amount(amount):
                """Scroll the applicant list by a specific amount."""
                try:
                    result = driver.execute_script("""
                        const lists = document.querySelectorAll('[class*="applicants__list"], [class*="hiring-applicant"]');
                        for (const list of lists) {
                            if (list.scrollHeight > list.clientHeight) {
                                list.scrollTop += arguments[0];
                                return {scrolled: true, scrollTop: list.scrollTop, scrollHeight: list.scrollHeight};
                            }
                        }
                        return {scrolled: false};
                    """, amount)
                    return result
                except Exception:
                    return {"scrolled": False}

            while total_processed < max_applicants and consecutive_no_progress < max_no_progress:
                try:
                    # Re-fetch cards each time (DOM changes due to virtual scrolling)
                    cards = get_applicant_cards()
                    processed_this_round = 0

                    # Process ALL unprocessed cards currently visible
                    for card in cards:
                        if total_processed >= max_applicants:
                            break
                        try:
                            aria = card.get_attribute('aria-label')
                            if not aria or ', Verified profile' not in aria:
                                continue

                            name = aria.replace(', Verified profile', '').strip()
                            if not name or name in processed_names:
                                continue

                            # Found an unprocessed card
                            processed_names.add(name)

                            # Scroll the card into view and click
                            driver.execute_script("""
                                arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});
                                arguments[0].click();
                            """, card)
                            time.sleep(0.4)

                            # Extract details from right panel
                            details = extract_applicant_details()
                            if not details["name"]:
                                details["name"] = name

                            # Click Contact and extract info
                            contact = click_contact_and_extract()

                            applicants_with_contact.append({
                                **details,
                                "phone": contact.get("phone"),
                                "email": contact.get("email")
                            })

                            total_processed += 1
                            processed_this_round += 1

                            if total_processed % 10 == 0:
                                print(f"Processed {total_processed}/{total_cards}: {name}", flush=True)
                            elif total_processed <= 5:
                                print(f"Processed {total_processed}: {name}", flush=True)

                            time.sleep(delay_seconds)

                        except StaleElementReferenceException:
                            continue
                        except Exception as e:
                            logger.debug(f"Error processing card: {e}")
                            continue

                    if processed_this_round > 0:
                        consecutive_no_progress = 0
                    else:
                        consecutive_no_progress += 1

                    # Always scroll down to reveal new cards
                    scroll_result = scroll_list_by_amount(scroll_increment)
                    scroll_position += scroll_increment
                    time.sleep(0.2)

                    if consecutive_no_progress > 0 and consecutive_no_progress % 10 == 0:
                        print(f"Scrolling to find more... ({total_processed} processed, scroll attempt {consecutive_no_progress}/{max_no_progress})", flush=True)

                except StaleElementReferenceException:
                    time.sleep(0.15)
                    continue
                except Exception as e:
                    logger.debug(f"Error in processing loop: {e}")
                    consecutive_no_progress += 1
                    continue

            if consecutive_no_progress >= max_no_progress:
                print(f"Finished - no more applicants found after {max_no_progress} scroll attempts", flush=True)

            # Summary
            phones_found = sum(1 for a in applicants_with_contact if a.get("phone"))
            emails_found = sum(1 for a in applicants_with_contact if a.get("email"))

            return {
                "job_id": job_id,
                "total_processed": len(applicants_with_contact),
                "phones_found": phones_found,
                "emails_found": emails_found,
                "applicants": applicants_with_contact
            }

        except Exception as e:
            return handle_tool_error(e, "get_applicants_with_contact")

    @mcp.tool()
    async def send_message_to_applicant(
        job_id: str,
        applicant_name: str,
        message: str
    ) -> Dict[str, Any]:
        """
        Send a LinkedIn message to a specific applicant from the hiring page.

        This tool finds an applicant by name on the hiring page and sends them
        a message using LinkedIn's built-in messaging.

        Args:
            job_id (str): LinkedIn job ID
            applicant_name (str): Full name of the applicant to message
            message (str): The message text to send

        Returns:
            Dict[str, Any]: Status of the message send operation
        """
        from selenium.webdriver.common.keys import Keys

        try:
            driver = safe_get_driver()

            # Navigate to the hiring applicants page
            url = f"https://www.linkedin.com/hiring/applicants/?jobId={job_id}"
            logger.info(f"Navigating to: {url}")
            driver.get(url)
            time.sleep(5)

            # Find the applicant by name
            applicant_found = False
            cards = driver.find_elements(
                By.XPATH,
                '//*[contains(@aria-label, ", Verified profile")]'
            )

            for card in cards:
                try:
                    aria = card.get_attribute('aria-label')
                    if aria and applicant_name.lower() in aria.lower():
                        # Click on the applicant
                        driver.execute_script("arguments[0].click();", card)
                        time.sleep(1.5)
                        applicant_found = True
                        break
                except StaleElementReferenceException:
                    continue

            if not applicant_found:
                return {
                    "status": "error",
                    "message": f"Could not find applicant: {applicant_name}",
                    "job_id": job_id
                }

            # Find and click the Message button
            message_btn = None
            msg_selectors = [
                '//button[contains(., "Message")]',
                '//button[@aria-label="Message"]',
                '//button[.//span[contains(text(), "Message")]]',
            ]

            for selector in msg_selectors:
                try:
                    btns = driver.find_elements(By.XPATH, selector)
                    for btn in btns:
                        if btn.is_displayed() and btn.is_enabled():
                            message_btn = btn
                            break
                    if message_btn:
                        break
                except Exception:
                    continue

            if not message_btn:
                return {
                    "status": "error",
                    "message": "Could not find Message button",
                    "applicant_name": applicant_name
                }

            # Click Message button
            driver.execute_script("arguments[0].click();", message_btn)
            time.sleep(2)

            # Find message input field and type message
            msg_input_selectors = [
                '//div[@role="textbox"]',
                '//textarea[contains(@class, "message")]',
                '//div[contains(@class, "msg-form__contenteditable")]',
            ]

            msg_input = None
            for selector in msg_input_selectors:
                try:
                    msg_input = driver.find_element(By.XPATH, selector)
                    if msg_input:
                        break
                except NoSuchElementException:
                    continue

            if not msg_input:
                return {
                    "status": "error",
                    "message": "Could not find message input field",
                    "applicant_name": applicant_name
                }

            # Type the message
            msg_input.click()
            msg_input.send_keys(message)
            time.sleep(1)

            # Find and click Send button
            send_btn = None
            send_selectors = [
                '//button[contains(., "Send")]',
                '//button[@type="submit"]',
                '//button[contains(@class, "send")]',
            ]

            for selector in send_selectors:
                try:
                    btns = driver.find_elements(By.XPATH, selector)
                    for btn in btns:
                        if btn.is_displayed() and btn.is_enabled():
                            send_btn = btn
                            break
                    if send_btn:
                        break
                except Exception:
                    continue

            if not send_btn:
                return {
                    "status": "error",
                    "message": "Could not find Send button",
                    "applicant_name": applicant_name
                }

            # Click Send
            driver.execute_script("arguments[0].click();", send_btn)
            time.sleep(2)

            return {
                "status": "success",
                "message": f"Message sent to {applicant_name}",
                "applicant_name": applicant_name,
                "message_sent": message[:100] + "..." if len(message) > 100 else message
            }

        except Exception as e:
            return handle_tool_error(e, "send_message_to_applicant")


def _extract_applicant_from_card(element, driver) -> Optional[Dict[str, Any]]:
    """
    Extract applicant data from an applicant card element.

    Args:
        element: Selenium WebElement for the applicant card
        driver: Selenium WebDriver instance

    Returns:
        Dict with applicant data or None if extraction failed
    """
    try:
        applicant_data = {
            "name": None,
            "headline": None,
            "location": None,
            "profile_url": None,
            "applied_date": None,
            "status": None
        }

        # Try to extract name
        name_selectors = [
            (By.CLASS_NAME, "hiring-applicants__name"),
            (By.CLASS_NAME, "applicant-name"),
            (By.CSS_SELECTOR, "[data-test-applicant-name]"),
            (By.CSS_SELECTOR, ".artdeco-entity-lockup__title"),
            (By.TAG_NAME, "h3"),
        ]
        for sel_type, sel_value in name_selectors:
            try:
                name_elem = element.find_element(sel_type, sel_value)
                applicant_data["name"] = name_elem.text.strip()
                break
            except NoSuchElementException:
                continue

        # Try to extract headline/title
        headline_selectors = [
            (By.CLASS_NAME, "hiring-applicants__headline"),
            (By.CLASS_NAME, "applicant-headline"),
            (By.CSS_SELECTOR, ".artdeco-entity-lockup__subtitle"),
        ]
        for sel_type, sel_value in headline_selectors:
            try:
                headline_elem = element.find_element(sel_type, sel_value)
                applicant_data["headline"] = headline_elem.text.strip()
                break
            except NoSuchElementException:
                continue

        # Try to extract profile URL
        try:
            link_elem = element.find_element(By.TAG_NAME, "a")
            href = link_elem.get_attribute("href")
            if href and "linkedin.com/in/" in href:
                applicant_data["profile_url"] = href.split("?")[0]  # Remove query params
        except NoSuchElementException:
            pass

        # Try to extract location
        location_selectors = [
            (By.CLASS_NAME, "hiring-applicants__location"),
            (By.CLASS_NAME, "applicant-location"),
            (By.CSS_SELECTOR, "[data-test-applicant-location]"),
        ]
        for sel_type, sel_value in location_selectors:
            try:
                location_elem = element.find_element(sel_type, sel_value)
                applicant_data["location"] = location_elem.text.strip()
                break
            except NoSuchElementException:
                continue

        # Try to extract application date
        date_selectors = [
            (By.CLASS_NAME, "hiring-applicants__applied-date"),
            (By.CLASS_NAME, "applicant-applied-date"),
            (By.CSS_SELECTOR, "[data-test-applied-date]"),
        ]
        for sel_type, sel_value in date_selectors:
            try:
                date_elem = element.find_element(sel_type, sel_value)
                applicant_data["applied_date"] = date_elem.text.strip()
                break
            except NoSuchElementException:
                continue

        # Only return if we got at least a name or profile URL
        if applicant_data["name"] or applicant_data["profile_url"]:
            return applicant_data

        return None

    except Exception as e:
        logger.warning(f"Error extracting applicant data: {e}")
        return None
