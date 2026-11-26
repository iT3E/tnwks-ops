// The token of the pod for each request to the server, read from the
// mounted Secret github-mcp-app-token at each request. The kubelet swaps
// the files of a Secret volume by a link when the token job writes a new
// token, so the next request takes it with no restart. An env var, or a
// value read once at start, would end with the token after one hour.
//
// No token, the placeholder of the Secret, or a value with a space or a
// control character: an empty value, so the proxy answers 503 and sends
// nothing on (fail closed), and one line in the error log that names the
// case, never the value.
const fs = require("fs");

const FILE = "/var/run/github-mcp-app-token/GITHUB_MCP_TOKEN";
const PLACEHOLDER = "pending";

function read(r) {
    let value;
    try {
        value = fs.readFileSync(FILE, "utf8").trim();
    } catch (e) {
        r.error("github-mcp-proxy token=missing");
        return "";
    }
    if (value === "" || value === PLACEHOLDER) {
        r.error("github-mcp-proxy token=pending");
        return "";
    }
    if (!/^[\x21-\x7e]+$/.test(value)) {
        r.error("github-mcp-proxy token=bad");
        return "";
    }
    return value;
}

export default { read };
