(() => {
  const params = new URLSearchParams(window.location.search);
  const activeTab = params.get("tab") || "users";

  const sections = document.querySelectorAll("[data-admin-section]");
  const navLinks = document.querySelectorAll("[data-admin-tab]");

  function setActive(tab) {
    sections.forEach((section) => {
      section.classList.toggle("active", section.dataset.adminSection === tab);
    });
    navLinks.forEach((link) => {
      link.classList.toggle("active", link.dataset.adminTab === tab);
    });
  }

  setActive(activeTab);

  const startingCredits = document.getElementById("sms-starting-credits");
  const monthlyNumberCost = document.getElementById("sms-monthly-number-cost");
  const sendCost = document.getElementById("sms-send-cost");
  const forwardCost = document.getElementById("sms-forward-cost");
  const suspendAfterDays = document.getElementById("sms-suspend-after-days");
  const saveBtn = document.getElementById("sms-pricing-save");
  const msg = document.getElementById("sms-pricing-msg");

  async function loadPricing() {
    if (!startingCredits) return;
    if (msg) msg.textContent = "Loading pricing…";
    try {
      const response = await fetch("/api/admin/sms_pricing", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Failed to load pricing (${response.status})`);
      }
      const data = await response.json();
      startingCredits.value = String(data.sms_starting_credits ?? 0);
      monthlyNumberCost.value = String(data.sms_monthly_number_cost ?? 0);
      sendCost.value = String(data.sms_send_cost ?? 0);
      forwardCost.value = String(data.sms_forward_cost ?? 0);
      suspendAfterDays.value = String(data.sms_suspend_after_days ?? 0);
      if (msg) msg.textContent = "";
    } catch (error) {
      if (msg) msg.textContent = "Failed to load SMS pricing.";
      console.error(error);
    }
  }

  async function savePricing() {
    if (msg) msg.textContent = "Saving…";
    try {
      const payload = {
        sms_starting_credits: Number(startingCredits?.value ?? 0),
        sms_monthly_number_cost: Number(monthlyNumberCost?.value ?? 0),
        sms_send_cost: Number(sendCost?.value ?? 0),
        sms_forward_cost: Number(forwardCost?.value ?? 0),
        sms_suspend_after_days: Number(suspendAfterDays?.value ?? 0),
      };
      const response = await fetch("/api/admin/sms_pricing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(`Failed to save pricing (${response.status})`);
      }
      if (msg) msg.textContent = "Pricing saved.";
    } catch (error) {
      if (msg) msg.textContent = "Failed to save SMS pricing.";
      console.error(error);
    }
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", savePricing);
  }

  if (activeTab === "sms") {
    loadPricing();
  }
})();
