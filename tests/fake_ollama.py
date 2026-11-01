"""Tiny OpenAI-compatible stand-in for a local Ollama model (for offline tests of the AI code path)."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        sys_ = body["messages"][0]["content"]; user = body["messages"][-1]["content"]
        if "requirements" in sys_ and "compare" in sys_:
            out = json.dumps({"requirements": [
                {"text": "5+ years of enterprise network engineering", "covered": True, "evidence": "Network Engineer with 6 years"},
                {"text": "BGP and OSPF routing", "covered": True, "evidence": "BGP and OSPF routing at Acme Health"},
                {"text": "Palo Alto firewalls", "covered": True, "evidence": "Palo Alto firewalls"},
                {"text": "Juniper Mist wireless", "covered": False, "evidence": "Profile shows Aruba wireless, not Juniper Mist"},
                {"text": "CCIE certification", "covered": False, "evidence": "Profile lists CCNP, not CCIE"}]})
        elif "edit a job-application document" in sys_:
            doc = user.split("DOCUMENT:\n", 1)[1]
            out = doc.replace("SUMMARY\n", "SUMMARY\nResults-focused network engineer. ")
        elif "resume" in sys_:
            out = ("Jordan Rivera\nDallas, TX | jordan.rivera@example.com\n\nSUMMARY\nNetwork Engineer with 6 years designing enterprise "
                   "LAN/WAN and SD-WAN networks.\n\nSKILLS\nBGP, OSPF, Palo Alto, Cisco, SD-WAN, Meraki, Ansible, Kubernetes\n\n"
                   "EXPERIENCE\nSenior Network Engineer - Acme Health (2021 - present)\n- BGP and OSPF routing across data centers\n"
                   "- Meraki SD-WAN rollout to 40 sites\n- Palo Alto firewall policy\nNetwork Administrator - Lone Star Logistics (2018 - 2021)\n"
                   "- VLANs, VPN, F5 load balancers\n")
        elif "cover letter" in sys_:
            out = "Dear Hiring Team,\n\nI'm applying for the Network Engineer role...\n\nSincerely,\nJordan Rivera"
        elif "email" in sys_:
            out = "Subject: Network Engineer application - Jordan Rivera\n\nHello,\n\nPlease find my resume attached.\n\nBest,\nJordan Rivera"
        else:
            out = "OK"
        r = json.dumps({"choices": [{"message": {"content": out}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(r)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", 11999), H).serve_forever()
