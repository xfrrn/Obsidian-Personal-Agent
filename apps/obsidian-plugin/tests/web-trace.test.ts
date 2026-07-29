import assert from "node:assert/strict"
import test from "node:test"
import { webFetchMethodSummary } from "../ui/src/web-trace"

test("summarizes local web fetches and provider fallback", () => {
  assert.equal(webFetchMethodSummary([{ fetch_method: "local", fallback_used: false }]), "本地静态抓取")
  assert.equal(webFetchMethodSummary([{ fetch_method: "tavily", fallback_used: true }]), "Tavily 回退")
  assert.equal(webFetchMethodSummary([
    { fetch_method: "local", fallback_used: false },
    { fetch_method: "exa", fallback_used: true },
  ]), "本地静态抓取 1 · Exa 回退 1")
  assert.equal(webFetchMethodSummary([{ domain: "example.com" }]), "")
})
