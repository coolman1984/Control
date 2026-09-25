"""Create a reusable xl2ai project skeleton from one or more source paths."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from .core.errors import Xl2aiError


def _slug(text):
    s=re.sub(r"[^\w-]+","-",str(text).strip(),flags=re.UNICODE).strip("-").lower()
    return s or "project"


def _toml_string(value):
    return json.dumps(str(value).replace("\\","/"), ensure_ascii=False)


def create_project(output, name, sources, wrapper_prefixes=(), with_pack=False, force=False):
    output=os.path.abspath(output)
    root=os.path.dirname(output)
    if os.path.exists(output) and not force:
        raise Xl2aiError("E_CONFIG",f"config already exists: {output}","pass --force only if you intend to replace it")
    os.makedirs(root,exist_ok=True)
    lines=["# xl2ai project configuration","[project]",f"name = {_toml_string(name)}",'data_dir = "data"',"",
           "[environment]",f"wrapper_prefixes = [{', '.join(_toml_string(x) for x in wrapper_prefixes)}]","",
           "[refresh]","allow_partial = false","keep_runs = 3","lock_stale_hours = 12","",
           "[extract]","verify = true","strict = false","block_cells = 500000","cache_cells = 12000000",
           "open_timeout = 180","visible = false","sheets = []","",
           "[analysis]","sample_values = 5","top_k = 5","relation_sample = 1000","row_hash_max_rows = 200000","",
           "[rules]",f"packs = [{_toml_string('packs/'+_slug(name)+'/pack.toml') if with_pack else ''}]",
           "block_on_error = false","",
           "[ai]","context_tokens = 4000","query_rows = 50","query_bytes = 8192","query_timeout = 5",""]
    alias_counts={}
    for src in sources:
        p=os.path.abspath(src)
        try:
            rel=os.path.relpath(p,root)
            shown=rel if not rel.startswith("..") else p
        except ValueError:
            shown=p
        source_lines=["[[sources]]",f"path = {_toml_string(shown)}"]
        if os.path.isfile(p) and not any(ch in src for ch in "*?"):
            base=_slug(os.path.splitext(os.path.basename(p))[0])
            alias_counts[base]=alias_counts.get(base,0)+1
            alias=base if alias_counts[base]==1 else f"{base}-{alias_counts[base]}"
            source_lines.append(f"alias = {_toml_string(alias)}")
        lines += source_lines+[""]
    with open(output,"w",encoding="utf-8",newline="\n") as f:
        f.write("\n".join(lines).rstrip()+"\n")
    pack_path=None
    if with_pack:
        pack_dir=os.path.join(root,"packs",_slug(name))
        os.makedirs(pack_dir,exist_ok=True)
        pack_path=os.path.join(pack_dir,"pack.toml")
        if not os.path.exists(pack_path) or force:
            with open(pack_path,"w",encoding="utf-8",newline="\n") as f:
                f.write(f'''[pack]\nname = {_toml_string(_slug(name))}\nversion = "1.0"\n\n'''
                        '# Add confirmed business language here.\n'
                        '# [[term]]\n# term = "Net Sales"\n# meaning = "..."\n# aliases = ["Sales"]\n# unit = "EGP"\n\n'
                        '# Rules and KPIs use a stable source alias/table selector. Give important sources aliases in xl2ai.toml.\n'
                        '# [[rule]]\n# id = "example"\n# sql = "SELECT COUNT(*) FROM {{table:source-alias/table_name}} WHERE ..."\n'
                        '# expect = "zero"\n# severity = "error"\n\n'
                        '# [[key]]\n# table = "source-alias/table_name"\n# columns = ["id"]\n\n'
                        '# [[relation]]\n# from_table = "source-alias/orders"\n# from_column = "customer_id"\n'
                        '# to_table = "source-alias/customers"\n# to_column = "customer_id"\n\n'
                        '# [[kpi]]\n# id = "example_kpi"\n# sql = "SELECT SUM(amount) FROM {{table:source-alias/table_name}}"\n# unit = "EGP"\n')
    return output,pack_path


def main(argv=None):
    ap=argparse.ArgumentParser(prog="xl2ai init",description="Create an xl2ai.toml project skeleton.")
    ap.add_argument("sources",nargs="+")
    ap.add_argument("--name",default=os.path.basename(os.getcwd()) or "project")
    ap.add_argument("--output",default="xl2ai.toml")
    ap.add_argument("--wrapper-prefix",action="append",default=[])
    ap.add_argument("--with-pack",action="store_true")
    ap.add_argument("--force",action="store_true")
    args=ap.parse_args(argv)
    try:
        cfg,pack=create_project(args.output,args.name,args.sources,args.wrapper_prefix,args.with_pack,args.force)
    except Xl2aiError as e:
        print(str(e),file=sys.stderr); return 1
    print(cfg)
    if pack: print(pack)
    print("next: python -m xl2ai doctor")
    print("then: python -m xl2ai refresh")
    return 0


if __name__=="__main__":
    sys.exit(main())
