## Supported Versions
### Django Nova is currently in the Alpha stage of its lifecycle. Security updates and patches are only applied to the most recent release.

Below is the matrix of the underlying Django framework versions that receive security fixes within Django Nova. Versions of Django outside of this scope are inherently unsupported by this toolkit.

Django Version	Supported	Notes
5.2.x	:white_check_mark:	Primary testing target (bleeding-edge)
5.1.x	:white_check_mark:	Fully supported
5.0.x	:white_check_mark:	Supported, but approaching End of Life (upstream)
< 5.0	:x:	Unsupported due to reliance on modern Python typing (PEP 695, 3.12+)
Reporting a Vulnerability
We take the security of Django Nova seriously. If you believe you have found a security vulnerability, please handle it responsibly.

How to Report:The preferred method is to use GitHub's built-in Private Vulnerability Reporting feature (click the "Security" tab at the top of this repository, then "Report a vulnerability").

If you cannot use GitHub, you can contact the maintainer directly via email: alimpievne@gmail.com with the subject line starting with [SECURITY].

What to Expect:

Acknowledgment: You will receive an acknowledgment of your report within 72 hours.
Assessment: We will validate the vulnerability and determine its severity (Critical, High, Medium, Low).
Resolution Timeline:
Critical/High: A patch will be released as soon as possible (target: within 7 days).
Medium/Low: A patch will be included in the next scheduled minor/patch release.
Disclosure: We adhere to coordinated disclosure. Please do not publicly disclose the vulnerability until a patch is released and users have had a reasonable time to update. We will keep you informed of the patch release timeline.
Thank you for helping keep Django Nova and the broader ecosystem safe.
