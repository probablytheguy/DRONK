"""Optional localhost DEMO browser QA; no USB devices or external requests."""
import sys
from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:8765"
assert url.startswith("http://127.0.0.1:")
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
                             args=["--no-sandbox","--disable-background-networking"])
    for name,width,height,mobile in [("desktop",1440,1100,False),("iphone",390,844,True)]:
        context=browser.new_context(viewport={"width":width,"height":height},is_mobile=mobile,has_touch=mobile)
        page=context.new_page(); errors=[]; external=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.on("request",lambda r: external.append(r.url) if not r.url.startswith(url) else None)
        page.goto(url,wait_until="domcontentloaded")
        page.wait_for_function("() => latest?.demo===true && document.getElementById('host-state').textContent==='STOPPED' && !document.getElementById('start').disabled")
        assert page.locator("#demo-banner").is_visible()
        assert page.locator("#map, #aircraft-table").count()==0
        page.locator('#start').click()
        page.wait_for_function("() => document.getElementById('host-state').textContent==='RUNNING' && document.querySelectorAll('#receivers .receiver-card').length===2")
        page.wait_for_function("() => document.getElementById('mode').textContent==='DEMO' && document.getElementById('logs').textContent.includes('SYNTH-DRONK')")
        assert "Reported uptime" in page.locator('#receivers').inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.wait_for_function("() => document.getElementById('detections').textContent.includes('Detected, Position Unavailable')")
        assert "SYNTH-NOFIX-03" in page.locator('#detections').inner_text()
        response=context.request.get(url+"/api/export?format=csv")
        assert response.status==200 and "SYNTH-DRONK" in response.text() and "SYNTH-NOFIX-03,SERIAL_NUMBER,DETECTED_NO_POSITION,False" in response.text()
        response=context.request.get(url+"/api/export?format=csv&table=history")
        assert response.status==200 and "SYNTH-DRONK" in response.text() and "SYNTH-NOFIX-03" not in response.text()
        page.screenshot(path=f"/tmp/dronk-{name}.png",full_page=True)
        page.evaluate("""() => {const d=structuredClone(latest);d.logs.unshift({time:'now',kind:'test',event:{value:'<img src=x onerror=alert(1)>'}});render(d);} """)
        assert page.locator("#logs img").count()==0
        assert '<img' in page.locator('#logs').inner_text()
        page.locator('#stop').click()
        page.wait_for_function("() => document.getElementById('host-state').textContent==='STOPPED'")
        assert page.locator('#mode').inner_text()=='DISCONNECTED'
        assert 'NOT OBSERVED' in page.locator('#receivers').inner_text()
        page.evaluate("events.close();events.onerror();")
        assert page.locator('#start').is_disabled()
        assert not external, external
        assert not errors, errors
        print(f"{name}: real host start/stop, logs, receiver state, export, safe text, mobile layout and disconnect passed")
        context.close()
    browser.close()
