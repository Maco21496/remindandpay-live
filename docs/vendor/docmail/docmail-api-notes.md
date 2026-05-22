# Docmail API Notes (from `docmail_guide_extracted.txt`)

Source basis: `docs/vendor/docmail/docmail_guide_extracted.txt` (Docmail Web Service v2.0, Dec 2024).

## 1) Authentication method
- SOAP endpoint described via WSDL at `https://api.docmail.co.uk/DMWS.asmx`.
- Standard calls require `Username` and `Password` parameters.
- Guide recommends temporary pass keys instead of repeatedly sending user password.
- Extended call method `GetUserLoginKey` returns:
  - `Pass key` (GUID)
  - `Expiry minutes`
- Pass key is then used as `Password` in subsequent calls.
- To request a pass key, the original account `Username` + real `Password` must be supplied to the Extended Call; pass keys cannot mint additional pass keys.
- API use requires Docmail-side authorisation by support:
  - Primary authorisation: test mailings only.
  - Secondary authorisation: production/live send.
- Account/user requirements:
  - Docmail account must exist and web-service access enabled.
  - Additional user must have “Can use web service” permission.

Security notes for env vars (integration guidance):
- Store Docmail username/password in secrets manager or environment variables.
- Use `GetUserLoginKey` at runtime; do not persist long-lived plain password in job metadata/logs.
- Treat pass key as secret; rotate per expiry window.

## 2) Test/live endpoints
- WSDL/service URL in guide: `https://api.docmail.co.uk/DMWS.asmx`.
- The extracted guide text does **not** list a separate sandbox hostname or separate test WSDL.
- Test behavior is controlled by mailing reference convention + account authorisation:
  - Prefix mailing `AccountRef` with `[Test]` to create a test mailing that will not be sent (example code notes this explicitly).
  - Primary authorisation mode permits test-only mailings.
- Extended call `SetErrorSimulation` supports testing error handling on test mailings.

SOAP endpoint URL(s):
- Not found in extracted guide text as a separate URL from WSDL; guide consistently references the same `.asmx` endpoint.

## 3) Core API operations (actual names in guide)
Mailing creation/setup
- `CreateFileMailing`
- `CreateMailing`
- `AddMailingFilter`
- `AddDeliveryAddress` (courier delivery only)
- `UpdateMailingOptions`

Documents / templates / pack content
- `AddTemplateFile`
- `AddTemplateBackgroundFile`
- `AddTemplateFromLibrary`
- `AddTemplateFromOrder`
- `AddMailPackFromLibrary`
- `AddMailPackFromOrder`
- `AddDesignerTemplate`
- `AddDesignerImage`
- `AddDesignerStoredImage`
- `AddDesignerText`
- `SetMailPackVariableValue`
- `SetTemplateVariableValue`

Recipients / address data
- `AddMailingListFile`
- `AddMailingListString`
- `AddMailingListFromLibrary`
- `AddMailingListFromOrder`
- `AddAddress`
- `AddSelf`
- `AutoCorrectAddresses`
- `SetMailingListProofOption`

Proof / process / approval / status
- `ProcessMailing`
- `GetMailingDetails`
- `GetStatus`
- `GetProofFile`
- `GetProofImage`
- `UserApproveMailing`
- `CancelMailingApproval`
- `ListProofPackDetails` (extended call)
- `GetProofPackFile` (extended call)
- `GetProofPackImage` (extended call)

Cancel/delete
- `DeleteTemplate`
- `DeleteMailPack`
- `DeleteMailingList`
- `DeleteMailing`
- `CancelMailing`

Balance/account
- `GetBalance`
- `GetMailingGUIDFromOrderRef`

Address count / estimate
- `AddMailingListFileForAddressCount`
- `AddMailingListStringForAddressCount`
- `PollMailingListForAddressCount`
- `GetMailingPriceEstimate`
- `GetPriceEstimate`

## 4) Recommended mailing flow (from guide call structure)
1. Authenticate (`Username`/`Password`) and get pass key via `GetUserLoginKey` (recommended).
2. Create mailing shell:
   - either `CreateFileMailing` (zip payload, optional XML control file)
   - or `CreateMailing` + subsequent add calls.
3. Add document/template content (`AddTemplateFile` etc.).
4. Add recipient data (`AddMailingListFile` / `AddMailingListString` / `AddAddress`).
5. Optionally run `AutoCorrectAddresses` and set proof address options (`SetMailingListProofOption`).
6. Call `ProcessMailing` (partial/proof-first flow is supported).
7. Poll `GetStatus` with reasonable delay (guide says avoid aggressive polling; minimum meaningful process including proof is ~10s).
8. Retrieve proof via `GetProofFile` / `GetProofImage` (or proof-pack calls when per-address/Dotpost proofing is enabled).
9. Finalise send:
   - either full process+payment path through `ProcessMailing`, and/or
   - user approval flow `UserApproveMailing` where account settings require proof approval.
10. Track with `GetMailingDetails` / `GetStatus` until terminal outcome.

## 5) Document/PDF requirements (what is explicitly stated)
- Accepted file formats include document/template and list formats; guide references:
  - XML list format (`DataList.xsd`)
  - Spreadsheet/data list imports
  - PDFs and password-protected docs (password handling required)
- Product/document types include values such as:
  - ProductType: `A4Letter`, `A3FoldedSheet` (and others listed)
  - DocumentType includes `A4Letter`, `A44PageBooklet` (and others listed)
- Print/mailing options include:
  - `IsMono` (mono vs colour)
  - `IsDuplex` (duplex vs simplex)
  - `DeliveryType` (`First` / `Standard`, courier options where applicable)
  - Envelope preference via `MinEnvelopeSize`: `Standard`, `C5`, `C4`, `C5Window`, or custom envelope GUID.
- Protected/encrypted files:
  - If encrypted/protected, relevant passwords must be supplied.

Not found in extracted guide text:
- A single consolidated “max upload file size” statement.
- A full one-page table of every page-size limit by product in the extracted snippets used here.

## 6) Recipient/address requirements
- `AddAddress` supports explicit recipient fields and `UseForProof` flag.
- Up to 3 addresses can be flagged for proofing.
- Address/list imports supported via file/string/XML DataList schema.
- `AutoCorrectAddresses` is available.
- Envelope window preview lines are available via proof pack details.

Not found in extracted guide text:
- A definitive simple “required fields minimal set” table in one place.
- A single global statement of postcode/country validation rules and UK-only/international constraints.

## 7) Proof/preview/final approval
- Proof retrieval calls:
  - `GetProofFile` (PDF)
  - `GetProofImage` (PNG)
- Proof-pack variants for per-address or Dotpost proofing:
  - `ListProofPackDetails`, `GetProofPackFile`, `GetProofPackImage`.
- Guide notes some scenarios require manual proof check/approval.
- `UserApproveMailing` exists for account/user-approval flows; `CancelMailingApproval` reverses approval state.
- `ProcessMailing` supports partial flow to proof stage.

Is proof mandatory?
- Conditional. The guide states certain account/workflow settings require proof/approval before send.

## 8) Status/error handling
- `GetStatus` returns processing status values (guide contains status list; examples include “Processing mailing - generating proof”).
- `GetMailingDetails` returns richer status/payment/proof detail fields.
- Error code section exists with numeric and named codes (examples in extracted text):
  - `-107 AccountActivationRequired`
  - `-110 InvalidXML`
  - `-116 IncorrectProtectedAreaPassword`
  - `-117 MaxPageLimit`
  - `-127 MaxClosedFacePageLimit`
- Return formats for success/failure are configurable: `Text`, `XML`, `JSON`, `JavaScript`.

Retryability guidance (integration interpretation based on calls/flows):
- Retryable candidates: transient processing failures/network issues while status non-terminal.
- Terminal candidates: schema invalid (`InvalidXML`), protected file password errors, page-limit violations, auth/account activation failures until corrected.

## 9) Cancellation/refund behaviour
- `CancelMailing` call exists.
- `DeleteMailing` exists (pre-submission lifecycle).
- Guide includes approval reversal (`CancelMailingApproval`).

Not found in extracted guide text:
- Clear refund-credit policy matrix by status stage.
- Exact production cut-off timestamps per product/service in the text excerpts used.

## 10) Payment/top-up/account-credit model (Docmail side)
- `GetBalance` indicates two Docmail account modes:
  - Top-up account: returns current balance.
  - Invoice account: returns credit limit, credit available, amount owed.
- `ProcessMailing` payment method behavior:
  - If unspecified and invoice-capable account, defaults to Invoice.
  - Otherwise payment from top-up credit.

RemindAndPay model alignment:
- End users should never interact with Docmail billing.
- RemindAndPay owns and funds the Docmail account.
- User-facing charge remains internal credits in `sms_credit_ledger`.

## 11) SOAP/XML implementation details
- Service is SOAP-based (`DMWS.asmx`).
- Response schema references:
  - `Result.xsd`
  - `DataList.xsd`
  - `DMWS.xsd` (for Create File Mailing XML config)
- `CreateFileMailing` supports zipped package including docs + addresses + XML control.
- Return format parameter is available on many calls.
- Extended calls provide additional methods (`GetUserLoginKey`, `GetZipStatus`, `SetErrorSimulation`, etc.).

SOAPAction names / full envelope examples:
- Not found in extracted guide text.

Python client approach (pragmatic)
- Use a SOAP client library (e.g., `zeep`) with explicit timeout/retry policy.
- Wrap Docmail operations in thin provider client methods reflecting guide call names.
- Keep request/response raw snippets in metadata for diagnostics (with secrets redacted).

## 12) RemindAndPay integration summary
- User spends RemindAndPay credits; Docmail is backend print/post supplier only.
- Create local `postal_jobs` records to track lifecycle from draft through provider submission and completion.
- Debit ledger once per postal job in `sms_credit_ledger` with deterministic `reference_id` (e.g., `postal:job:<id>`).
- Store Docmail IDs (`MailingGUID`, list/template GUIDs as relevant) in postal job provider metadata.
- Use proof-first + explicit confirm flow where possible before irreversible send.

## Unknowns explicitly marked
Where details above are marked “Not found in extracted guide text”, they should be validated in:
- the original PDF (`docs/vendor/docmail/docmail_guide.pdf`) and/or
- live WSDL operation metadata / vendor support clarification.
