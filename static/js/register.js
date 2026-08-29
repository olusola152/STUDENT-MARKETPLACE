// Show only the fields belonging to the selected account type.
(function () {
  var form = document.getElementById("signup");
  if (!form) return;
  var groups = form.querySelectorAll(".field-group");

  function sync() {
    var picked = form.querySelector('input[name="role"]:checked');
    var role = picked ? picked.value : "company";
    groups.forEach(function (group) {
      var mine = group.dataset.role === role;
      group.hidden = !mine;
      group.querySelectorAll("input, select").forEach(function (el) {
        el.disabled = !mine;
      });
    });
  }

  form.querySelectorAll('input[name="role"]').forEach(function (r) {
    r.addEventListener("change", sync);
  });
  sync();
})();
