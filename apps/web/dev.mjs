import {context} from "esbuild";
import {mkdir,writeFile,readFile} from "node:fs/promises";

await mkdir("public",{recursive:true});
const html = (await readFile("index.html","utf8")).replace("</body>",
    '<script>new EventSource("/esbuild").addEventListener("change",()=>location.reload())</script></body>');
await writeFile("public/index.html",html);
const build = await context({entryPoints:["src/main.tsx"],bundle:true,sourcemap:true,outfile:"public/app.js",
    define:{"process.env.NODE_ENV":'"development"'}});
await build.watch();
await build.serve({host:"0.0.0.0",port:8081,servedir:"public",fallback:"public/index.html"});
