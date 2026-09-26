# Clinical job discovery

Run Scraper uses the active CV's development technologies to search for EHR,
clinical software, patient records, and telehealth roles. The description must
establish clinical-system work and match at least two distinct CV development
skills, including a core technology: React, Angular, Node.js, Python, or Next.js.
React/React.js and other aliases count as one skill. Generic healthcare employer
names and medical benefits alone do not qualify a listing.

Keep hybrid roles in Bengaluru/Bangalore and remote roles worldwide where India
is not excluded. Onsite roles are excluded. Filtered results remain stored as
SKIPPED with a reason; APPLIED history is preserved. Scoring uses the same rules.
Existing historical rows remain visible in the All view.

The sample Greenhouse and Lever demo sources are disabled. LinkedIn uses the
hybrid filter for Bangalore and remote filter worldwide. Naukri uses clinical
technology queries with hybrid/remote locations and requires readable job detail
pages; missing clinical or technology evidence fails the filter. These are
deterministic text checks, so sparse or unusually worded descriptions may be
skipped. Search coverage depends on the configured portals and their access limits.

The scope is explicit on UserProfile (`clinical_systems_only`) and enabled by the
API, scoring CLI, discovery CLI with scoring, and automation pipeline. Standalone
matching utilities retain their general-purpose default. No FHIR/HL7 expertise is
added to the candidate profile merely because those terms identify clinical work.
