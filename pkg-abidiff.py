#!/usr/bin/python
###############################################################
# Package ABI Diff 0.95
# Verify API/ABI compatibility of Linux packages (RPM or DEB)
#
# Copyright (C) 2016 Andrey Ponomarenko's ABI Laboratory
#
# Written by Andrey Ponomarenko
#
# PLATFORMS
# =========
#  Linux
#
# REQUIREMENTS
# ============
#  Python 2
#  ABI Compliance Checker (1.99.24 or newer)
#  ABI Dumper (0.99.18 or newer)
#  Universal Ctags
#  GNU Binutils
#  Elfutils
#  G++
#
###############################################################
import argparse
import re
import sys
import os
import tempfile
import shutil
import signal
import subprocess

TOOL_VERSION = "0.95"

ABI_CC_VER = "1.99.24"
ABI_DUMPER_VER = "0.99.18"

PKGS = {}
PKGS_ATTR = {}
FILES = {}
PUBLIC_ABI = False

ARGS = {}
MOD_DIR = None

TMP_DIR = tempfile.mkdtemp()
ORIG_DIR = os.getcwd()

CMD_NAME = os.path.basename(__file__)

def print_err(msg):
    sys.stderr.write(msg+"\n")

def get_modules():
    tool_path = os.path.realpath(__file__)
    tool_dir = os.path.dirname(tool_path)
    
    dirs = [
        tool_dir,
        tool_dir+"/../share/pkg-abidiff"
    ]
    for d in dirs:
        if os.path.exists(d+"/modules"):
            return d+"/modules"
    
    print_err("ERROR: can't find modules")
    s_exit(1)

def s_exit(code):
    shutil.rmtree(TMP_DIR)
    sys.exit(code)

def int_exit(signal, frame):
    s_exit(1)

def extract_pkg(age, kind):
    global PKGS, TMP_DIR
    pkg = PKGS[age][kind]
    
    m = re.match(r".*\.(\w+)\Z", os.path.basename(pkg))
    fmt = None
    
    if m:
        fmt = m.group(1)
    
    if not m or fmt not in ["rpm", "deb"]:
        print_err("ERROR: unknown format of package \'"+pkg+"\'")
        s_exit(1)
    
    extr_dir = TMP_DIR+"/ext/"+age+"/"+kind
    
    if not os.path.exists(extr_dir):
        os.makedirs(extr_dir)
    
    pkg_abs = os.path.abspath(pkg)
    
    os.chdir(extr_dir)
    if fmt=="rpm":
        subprocess.call("rpm2cpio \""+pkg_abs+"\" | cpio -id --quiet", shell=True)
    elif fmt=="deb":
        subprocess.call(["dpkg-deb", "--extract", pkg_abs, "."])
    os.chdir(ORIG_DIR)
    
    return extr_dir

def get_rel_path(path):
    global TMP_DIR
    path = path.replace(TMP_DIR+"/", "")
    path = re.sub(r"\Aext/(old|new)/(rel|debug|devel)/", "", path)
    return path

def is_shared(path):
    return re.match(r"lib.*\.so(\..+|\Z)", path)

def get_fmt(path):
    m = re.match(r".*\.([^\.]+)\Z", path)
    if m:
        return m.group(1)
    
    return None

def get_attrs(path):
    fmt = get_fmt(path)
    
    name = None
    ver = None
    rl = None
    arch = None
    
    if fmt=="rpm":
        r = subprocess.check_output(["rpm", "-qp", "--queryformat", "%{name},%{version},%{release},%{arch}", path])
        name, ver, rl, arch = r.split(",")
        ver = ver+"-"+rl
    elif fmt=="deb":
        r = subprocess.check_output(["dpkg", "-f", path])
        attr = {"Package":None, "Version":None, "Architecture":None}
        for line in r.split("\n"):
            m = re.match(r"(\w+)\s*:\s*(.+)", line)
            if m:
                attr[m.group(1)] = m.group(2)
        
        name = attr["Package"]
        ver = attr["Version"]
        arch = attr["Architecture"]
    
    if name is not None and ver is not None and arch is not None:
        return [name, ver, arch]
    
    return None

def get_soname(path):
    r = subprocess.check_output(["objdump", "-p", path])
    m = re.search(r"SONAME\s+([^ ]+)", r)
    if m:
        return m.group(1).rstrip()
    
    return None

def get_short_name(obj):
    m = re.match(r"(.+\.so)(\..+|\Z)", obj)
    if m:
        return m.group(1)
    
    return None

def read_file(path):
    f = open(path, 'r')
    content = f.read()
    f.close()
    return content

def read_line(path):
    f = open(path, 'r')
    content = f.readline()
    f.close()
    return content

def write_file(path, content):
    f = open(path, 'w')
    f.write(content)
    f.close()

def read_stat(path, rdir):
    stat = {}
    line = read_line(path)
    for e in line.split(";"):
        m = re.search(r"(\w+):([^\s]+)", e)
        if m:
            stat[m.group(1)] = m.group(2)
    
    total = 0
    for k in stat:
        if k.find("_problems_")!=-1 or k=="changed_constants":
            total += int(stat[k])
    
    stat["total"] = total
    
    rpath = path.replace(rdir+"/", "")
    stat["path"] = rpath
    
    return stat

def compose_html_head(title, keywords, description):
    styles = read_file(MOD_DIR+"/Internals/Styles/Report.css")
    
    cnt =  "<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\" \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">\n"
    cnt += "<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\">\n"
    cnt += "<head>\n"
    cnt += "<meta http-equiv=\"Content-Type\" content=\"text/html; charset=utf-8\" />\n"
    cnt += "<meta name=\"keywords\" content=\""+keywords+"\" />\n"
    cnt += "<meta name=\"description\" content=\""+description+"\" />\n\n"

    cnt += "<title>\n"
    cnt += "    "+title+"\n"
    cnt += "</title>\n\n"
    
    cnt += "<style type=\"text/css\">\n"
    cnt += styles
    cnt += "</style>\n"

    cnt += "</head>\n"
    
    return cnt

def get_bc_class(rate, total):
    cclass = "ok"
    if float(rate)==100:
        if total:
            cclass = "warning"
    else:
        if float(rate)>=90:
            cclass = "warning"
        else:
            cclass = "incompatible"
    
    return cclass

def format_num(num):
    num = re.sub(r"\A(\d+\.\d\d).*\Z", r"\1", str(num))
    num = re.sub(r"\A(\d+)\.0\Z", r"\1", num)
    num = re.sub(r"(\.\d)0\Z", r"\1", num)
    return num

def get_dumpversion(prog):
    ver = subprocess.check_output([prog, "-dumpversion"])
    return ver.rstrip()

def cmp_vers(x, y):
    xp = x.split(".")
    yp = y.split(".")
    
    for k in range(len(xp), max(len(xp), len(yp))):
        xp.append("0")
    
    for k in range(len(yp), max(len(xp), len(yp))):
        yp.append("0")
    
    for k in range(0, len(xp)):
        a = xp[k]
        b = yp[k]
        
        if a==b:
            continue
        
        if(int(a) > int(b)):
            return 1
        else:
            return -1
    
    return 0

def is_empty_dump(path):
    empty = False
    f = open(path, 'r')
    for line in f:
        if line.find("'SymbolInfo'")!=-1:
            empty = (line.find("'SymbolInfo' => {}")!=-1)
            break
    
    f.close()
    return empty

def count_symbols(path, obj, age):
    print "Counting symbols in the ABI dump for "+os.path.basename(obj)+" ("+age+")"
    count = subprocess.check_output(["abi-compliance-checker", "-count-symbols", path])
    return int(count.rstrip())

def scenario():
    signal.signal(signal.SIGINT, int_exit)
    
    global MOD_DIR
    MOD_DIR = get_modules()
    
    desc = "Check backward API/ABI compatibility of Linux packages (RPM or DEB)"
    parser = argparse.ArgumentParser(description=desc, epilog="example: "+CMD_NAME+" -old P1 P1-DEBUG P1-DEV -new P2 P2-DEBUG P2-DEV")
    
    parser.add_argument('-v', action='version', version='Package ABI Diff (Pkg-ABIdiff) '+TOOL_VERSION)
    parser.add_argument('-old', help='list of old packages (package itself, debug-info and devel package)', nargs='*', metavar='PATH')
    parser.add_argument('-new', help='list of new packages (package itself, debug-info and devel package)', nargs='*', metavar='PATH')
    parser.add_argument('-o', '-report-dir', help='specify a directory to save report (default: ./compat_report)', metavar='DIR')
    parser.add_argument('-dumps-dir', help='specify a directory to save and reuse ABI dumps (default: ./abi_dump)', metavar='DIR')
    parser.add_argument('-bin', help='check binary compatibility only', action='store_true')
    parser.add_argument('-src', help='check source compatibility only', action='store_true')
    parser.add_argument('-rebuild', help='rebuild ABI dumps and report', action='store_true')
    parser.add_argument('-rebuild-report', help='rebuild report only', action='store_true')
    parser.add_argument('-rebuild-dumps', help='rebuild ABI dumps only', action='store_true')
    parser.add_argument('-ignore-tags', help='optional file with tags to ignore by ctags', metavar='PATH')
    parser.add_argument('-keep-registers-and-offsets', help='dump used registers and stack offsets even if incompatible build options detected', action='store_true')
    parser.add_argument('-use-tu-dump', help='use g++ syntax tree instead of ctags to list symbols in headers', action='store_true')
    parser.add_argument('-include-preamble', help='specify preamble headers (separated by semicolon)', metavar='PATHS')
    parser.add_argument('-include-paths', help='specify include paths (separated by semicolon)', metavar='PATHS')
    
    global ARGS
    ARGS = parser.parse_args()
    
    if not ARGS.old:
        print_err("ERROR: old packages are not specified (-old option)")
        s_exit(1)
    
    if not ARGS.new:
        print_err("ERROR: new packages are not specified (-new option)")
        s_exit(1)
    
    if cmp_vers(get_dumpversion("abi-compliance-checker"), ABI_CC_VER)<0:
        print_err("ERROR: the version of ABI Compliance Checker should be "+ABI_CC_VER+" or newer")
        s_exit(1)
    
    if cmp_vers(get_dumpversion("abi-dumper"), ABI_DUMPER_VER)<0:
        print_err("ERROR: the version of ABI Dumper should be "+ABI_DUMPER_VER+" or newer")
        s_exit(1)
    
    if not ARGS.bin and not ARGS.src:
        ARGS.bin = True
        ARGS.src = True
    
    if ARGS.rebuild:
        ARGS.rebuild_dumps = True
        ARGS.rebuild_report = True
    
    LIST = {}
    LIST["old"] = ARGS.old
    LIST["new"] = ARGS.new
    
    global PKGS
    PKGS["old"] = {}
    PKGS["new"] = {}
    
    global PKGS_ATTR
    PKGS_ATTR["old"] = {}
    PKGS_ATTR["new"] = {}
    
    for age in ["old", "new"]:
        for pkg in LIST[age]:
            if not os.path.exists(pkg):
                print_err("ERROR: can't access '"+pkg+"'")
                s_exit(1)
    
    for age in ["old", "new"]:
        parch = {}
        pname = {}
        pver = {}
        for pkg in LIST[age]:
            fmt = get_fmt(pkg)
            
            if fmt is None or fmt!="rpm" and fmt!="deb":
                print_err("ERROR: unknown format of package "+pkg)
                s_exit(1)
            
            fname = os.path.basename(pkg)
            kind = "rel"
            
            if re.match(r".*-(headers-|devel-|dev-|dev_).*", fname):
                kind = "devel"
            elif re.match(r".*-(debuginfo-|dbg_).*", fname):
                kind = "debug"
            
            PKGS[age][kind] = pkg
            attrs = get_attrs(pkg)
            if attrs:
                pname[kind] = attrs[0]
                pver[kind] = attrs[1]
                parch[kind] = attrs[2]
            else:
                print_err("ERROR: can't read attributes of a package "+pkg)
                s_exit(1)
        
        if "rel" not in PKGS[age]:
            print_err("ERROR: "+age+" release package is not specified")
            s_exit(1)
        
        if "debug" not in PKGS[age]:
            print_err("ERROR: "+age+" debuginfo package is not specified")
            s_exit(1)
        
        if pver["rel"]!=pver["debug"]:
            print "WARNING: versions of packages are not equal ("+age+")"
        
        if "devel" in pver:
            if pver["rel"]!=pver["devel"] or pver["debug"]!=pver["devel"]:
                print "WARNING: versions of packages are not equal ("+age+")"
        
        if parch["rel"]!=parch["debug"]:
            print_err("WARNING: architectures of packages are not equal ("+age+")")
            s_exit(1)
        
        if "devel" in parch:
            if parch["rel"]!=parch["devel"] or parch["debug"]!=parch["devel"]:
                print "WARNING: architectures of packages are not equal ("+age+")"
        
        pname["debug"] = re.sub(r"\-(debuginfo|dbg)\Z", "", pname["debug"])
        
        PKGS_ATTR[age]["name"] = pname["debug"]
        PKGS_ATTR[age]["ver"] = pver["debug"]
        PKGS_ATTR[age]["arch"] = parch["debug"]
    
    if PKGS_ATTR["old"]["name"]!=PKGS_ATTR["new"]["name"]:
        print "WARNING: names of old and new packages are not equal"
    
    if PKGS_ATTR["old"]["arch"]!=PKGS_ATTR["new"]["arch"]:
        print_err("ERROR: architectures of old and new packages are not equal")
        s_exit(1)
    
    global PUBLIC_ABI
    if "devel" in PKGS["old"]:
        if "devel" in PKGS["new"]:
            PUBLIC_ABI = True
        else:
            print_err("ERROR: new devel package is not specified")
            s_exit(1)
    elif "devel" in PKGS["new"]:
        print_err("ERROR: old devel package is not specified")
        s_exit(1)
    
    print "Extracting packages ..."
    global FILES
    FILES["old"] = {}
    FILES["new"] = {}
    for age in ["old", "new"]:
        for kind in ["rel", "debug", "devel"]:
            if kind not in PKGS[age]:
                continue
            
            e_dir = extract_pkg(age, kind)
            for root, dirs, files in os.walk(e_dir):
                for f in files:
                    fpath = root+"/"+f
                    
                    if os.path.islink(fpath):
                        continue
                    
                    fkind = None
                    if kind=="rel":
                        if is_shared(f):
                            fkind = "object"
                    elif kind=="debug":
                        if re.match(r".*\.debug\Z", f):
                            fkind = "debuginfo"
                        
                        if get_fmt(PKGS[age]["debug"])=="deb":
                            if is_shared(f):
                                fkind = "debuginfo"
                    elif kind=="devel":
                        if not re.match(r".*\.(pc)\Z", f):
                            fkind = "header"
                    
                    if fkind:
                        if fkind not in FILES[age]:
                            FILES[age][fkind] = {}
                        FILES[age][fkind][fpath] = 1
                    
                    if kind not in FILES[age]:
                        FILES[age][kind] = {}
                    FILES[age][kind][fpath] = 1
    
    abi_dump = {}
    soname = {}
    short_name = {}
    
    for age in ["old", "new"]:
        print "Creating ABI dumps ("+age+") ..."
        if "debuginfo" not in FILES[age]:
            print_err("ERROR: debuginfo files are not found in "+age+" debuginfo package")
            s_exit(1)
        
        if "object" not in FILES[age]:
            print_err("ERROR: shared objects are not found in "+age+" release package")
            s_exit(1)
        
        objects = FILES[age]["object"].keys()
        objects.sort(key=lambda x: x.lower())
        
        hdir = TMP_DIR+"/ext/"+age+"/devel"
        ddir = TMP_DIR+"/ext/"+age+"/debug"
        odir = TMP_DIR+"/ext/"+age+"/rel"
        
        abi_dump[age] = {}
        soname[age] = {}
        short_name[age] = {}
        
        parch = PKGS_ATTR[age]["arch"]
        pname = PKGS_ATTR[age]["name"]
        pver = PKGS_ATTR[age]["ver"]
        
        dump_dir = "abi_dump"
        if ARGS.dumps_dir:
            dump_dir = ARGS.dumps_dir
        
        dump_dir += "/"+parch+"/"+pname+"/"+pver
        print "Using dumps directory: "+dump_dir
        
        for obj in objects:
            oname = os.path.basename(obj)
            
            soname[age][oname] = get_soname(obj)
            short_name[age][oname] = get_short_name(oname)
            
            obj_dump_path = dump_dir+"/"+oname+"/ABI.dump"
            
            if os.path.exists(obj_dump_path):
                if ARGS.rebuild_dumps:
                    os.remove(obj_dump_path)
                else:
                    print "Using existing ABI dump for "+oname
                    abi_dump[age][oname] = obj_dump_path
                    continue
            
            print "Creating ABI dump for "+oname
            
            cmd_d = ["abi-dumper", "-o", obj_dump_path, "-lver", pver]
            
            cmd_d.append("-search-debuginfo")
            cmd_d.append(ddir)
            
            if "header" in FILES[age]:
                cmd_d.append("-public-headers")
                cmd_d.append(hdir)
            
            if ARGS.use_tu_dump:
                cmd_d.append("-use-tu-dump")
                if ARGS.include_preamble:
                    cmd_d.append("-include-preamble")
                    cmd_d.append(ARGS.include_preamble)
                if ARGS.include_paths:
                    cmd_d.append("-include-paths")
                    cmd_d.append(ARGS.include_paths)
            elif ARGS.ignore_tags:
                cmd_d.append("-ignore-tags")
                cmd_d.append(ARGS.ignore_tags)
            
            if ARGS.keep_registers_and_offsets:
                cmd_d.append("-keep-registers-and-offsets")
            
            cmd_d.append(obj)
            
            with open(TMP_DIR+"/log", "w") as log:
                subprocess.call(cmd_d, stdout=log)
            
            if not os.path.exists(obj_dump_path):
                print_err("ERROR: failed to create ABI dump for object "+oname+" ("+age+")")
                s_exit(1)
            
            if is_empty_dump(obj_dump_path):
                print "WARNING: empty ABI dump for "+oname+" ("+age+")"
            else:
                abi_dump[age][oname] = obj_dump_path
        
    print "Comparing ABIs ..."
    soname_r = {}
    short_name_r = {}
    
    for age in ["old", "new"]:
        soname_r[age] = {}
        for obj in soname[age]:
            sname = soname[age][obj]
            if sname not in soname_r[age]:
                soname_r[age][sname] = {}
            soname_r[age][sname][obj] = 1
        
        short_name_r[age] = {}
        for obj in short_name[age]:
            shname = short_name[age][obj]
            if shname not in short_name_r[age]:
                short_name_r[age][shname] = {}
            short_name_r[age][shname][obj] = 1
    
    old_objects = abi_dump["old"].keys()
    new_objects = abi_dump["new"].keys()
    
    if objects and not old_objects:
        print_err("ERROR: all ABI dumps are empty")
        s_exit(0)
    
    old_objects.sort(key=lambda x: x.lower())
    new_objects.sort(key=lambda x: x.lower())
    
    mapped = {}
    mapped_r = {}
    removed = {}
    
    report_dir = None
    if ARGS.o:
        report_dir = ARGS.o
    else:
        report_dir = "compat_report"
        report_dir += "/"+PKGS_ATTR["old"]["arch"]+"/"+PKGS_ATTR["old"]["name"]
        report_dir += "/"+PKGS_ATTR["old"]["ver"]+"/"+PKGS_ATTR["new"]["ver"]
    
    if os.path.exists(report_dir):
        if ARGS.rebuild_report:
            if os.path.exists(report_dir+"/index.html"):
                os.remove(report_dir+"/index.html")
        else:
            print "The report already exists: "+report_dir
            s_exit(0)
    
    compat = {}
    renamed_object = {}
    for obj in old_objects:
        new_obj = None
        
        # match by SONAME
        if obj in soname["old"]:
            sname = soname["old"][obj]
            bysoname = soname_r["new"][sname].keys()
            if bysoname and len(bysoname)==1:
                new_obj = bysoname[0]
        
        # match by name
        if new_obj is None:
            if obj in new_objects:
                new_obj = obj
        
        # match by short name
        if new_obj is None:
            if obj in short_name["old"]:
                shname = short_name["old"][obj]
                byshort = short_name_r["new"][shname].keys()
                if byshort and len(byshort)==1:
                    new_obj = byshort[0]
        
        if new_obj is None:
            removed[obj] = 1
            continue
        
        mapped[obj] = new_obj
        mapped_r[new_obj] = obj
    
    added = {}
    for obj in new_objects:
        if obj not in mapped_r:
            added[obj] = 1
    
    # one object
    if not mapped:
        if len(old_objects)==1 and len(new_objects)==1:
            obj = old_objects[0]
            new_obj = new_objects[0]
            
            mapped[obj] = new_obj
            renamed_object[obj] = new_obj
            
            removed.pop(obj, None)
            added.pop(new_obj, None)
    
    mapped_objs = mapped.keys()
    mapped_objs.sort(key=lambda x: x.lower())
    for obj in mapped_objs:
        new_obj = mapped[obj]
        
        if obj not in abi_dump["old"]:
            continue
        
        if new_obj not in abi_dump["new"]:
            continue
        
        print "Comparing "+obj+" (old) and "+new_obj+" (new)"
        
        obj_report_dir = report_dir+"/"+obj
        
        if os.path.exists(obj_report_dir):
            shutil.rmtree(obj_report_dir)
        
        bin_report = obj_report_dir+"/abi_compat_report.html"
        src_report = obj_report_dir+"/src_compat_report.html"
        
        cmd_c = ["abi-compliance-checker", "-l", obj, "-component", "object"]
        
        if ARGS.bin:
            cmd_c.append("-bin")
            cmd_c.extend(["-bin-report-path", bin_report])
        if ARGS.src:
            cmd_c.append("-src")
            cmd_c.extend(["-src-report-path", src_report])
        
        cmd_c.append("-old")
        cmd_c.append(abi_dump["old"][obj])
        
        cmd_c.append("-new")
        cmd_c.append(abi_dump["new"][new_obj])
        
        with open(TMP_DIR+"/log", "w") as log:
            subprocess.call(cmd_c, stdout=log)
        
        if ARGS.bin:
            if not os.path.exists(bin_report):
                print_err("ERROR: failed to create BC report for object "+obj)
                continue
        
        if ARGS.src:
            if not os.path.exists(src_report):
                print_err("ERROR: failed to create SC report for object "+obj)
                continue
        
        compat[obj] = {}
        res = []
        
        if ARGS.bin:
            compat[obj]["bin"] = read_stat(bin_report, report_dir)
            res.append("BC: "+format_num(100-float(compat[obj]["bin"]["affected"]))+"%")
        
        if ARGS.src:
            compat[obj]["src"] = read_stat(src_report, report_dir)
            res.append("SC: "+format_num(100-float(compat[obj]["src"]["affected"]))+"%")
        
        print ", ".join(res)
    
    if objects and not compat:
        print_err("ERROR: failed to create reports for objects")
        s_exit(0)
    
    object_symbols = {}
    changed_soname = {}
    for obj in mapped:
        new_obj = mapped[obj]
        
        old_soname = soname["old"][obj]
        new_soname = soname["new"][new_obj]
        
        if old_soname and new_soname and old_soname!=new_soname:
            changed_soname[obj] = new_soname
    
    # JSON report
    affected_t = 0
    problems_t = 0
    
    added_t = 0
    removed_t = 0
    
    affected_t_src = 0
    problems_t_src = 0
    
    total_funcs = 0
    
    for obj in compat:
        if ARGS.bin:
            report = compat[obj]["bin"]
        else:
            report = compat[obj]["src"]
        
        old_dump = abi_dump["old"][obj]
        funcs = count_symbols(old_dump, obj, "old")
        object_symbols[obj] = funcs
        
        affected_t += float(report["affected"])*funcs
        problems_t += int(report["total"])
        
        added_t += int(report["added"])
        removed_t += int(report["removed"])
        
        if ARGS.src:
            report_src = compat[obj]["src"]
            affected_t_src += float(report_src["affected"])*funcs
            problems_t_src += int(report_src["total"])
        
        total_funcs += funcs
    
    removed_by_objects_t = 0
    
    for obj in removed:
        old_dump = abi_dump["old"][obj]
        removed_by_objects_t += count_symbols(old_dump, obj, "old")
    
    bc = 100
    
    if total_funcs:
        bc -= affected_t/total_funcs
    
    if ARGS.src:
        bc_src = 100
        if total_funcs:
            bc_src -= affected_t_src/total_funcs
    
    if old_objects and removed:
        delta = (1-(removed_by_objects_t/(total_funcs+removed_by_objects_t)))
        bc *= delta
        if ARGS.src:
            bc_src *= delta
    
    bc = format_num(bc)
    
    if ARGS.src:
        bc_src = format_num(bc_src)
    
    meta = []
    if ARGS.bin:
        meta.append("\"BC\": "+str(bc))
    if ARGS.src:
        meta.append("\"Source_BC\": "+str(bc_src))
    meta.append("\"Added\": "+str(added_t))
    meta.append("\"Removed\": "+str(removed_t))
    if ARGS.bin:
        meta.append("\"TotalProblems\": "+str(problems_t))
    if ARGS.src:
        meta.append("\"Source_TotalProblems\": "+str(problems_t_src))
    meta.append("\"ObjectsAdded\": "+str(len(added)))
    meta.append("\"ObjectsRemoved\": "+str(len(removed)))
    meta.append("\"ChangedSoname\": "+str(len(changed_soname)))
    
    write_file(report_dir+"/meta.json", "{\n  "+",\n  ".join(meta)+"\n}\n")
    
    # HTML report
    n1 = PKGS_ATTR["old"]["name"]
    n2 = PKGS_ATTR["new"]["name"]
    
    v1 = PKGS_ATTR["old"]["ver"]
    v2 = PKGS_ATTR["new"]["ver"]
    
    arch = PKGS_ATTR["old"]["arch"]
    
    report = "<h1>ABI report"
    if n1==n2:
        title = n1+": API/ABI report between "+v1+" and "+v2+" versions"
        keywords = n1+", API, ABI, changes, compatibility, report"
        desc = "API/ABI compatibility report between "+v1+" and "+v2+" versions of the "+n1
        report += " for "+n1+": <u>"+v1+"</u> vs <u>"+v2+"</u>"
    else:
        title = "API/ABI report between "+n1+"-"+v1+" and "+n2+"-"+v2+" packages"
        keywords = n1+", "+n2+", API, ABI, changes, compatibility, report"
        desc = "API/ABI compatibility report between "+n1+"-"+v1+" and "+n2+"-"+v2+" packages"
        report += " for <u>"+n1+"-"+v1+"</u> vs <u>"+n2+"-"+v2+"</u>"
    
    if not ARGS.bin:
        report += " (source compatibility)"
    
    report += "</h1>\n"
    
    report += "<h2>Test Info</h2>\n"
    report += "<table class='summary'>\n"
    report += "<tr>\n"
    report += "<th class='left'>Package</th><td class='right'>"+n1+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<th class='left'>Old Version</th><td class='right'>"+v1+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<th class='left'>New Version</th><td class='right'>"+v2+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<th class='left'>Arch</th><td class='right'>"+arch+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    if PUBLIC_ABI:
        report += "<th class='left'>Subject</th><td class='right'>Public ABI</td>\n"
    else:
        report += "<th class='left'>Subject</th><td class='right'>Public ABI +<br/>Private ABI</td>\n"
    report += "</tr>\n"
    report += "</table>\n"
    
    report += "<h2>Test Result</h2>\n"
    report += "<span class='result'>\n"
    if ARGS.bin:
        report += "Binary compatibility: <span class='"+get_bc_class(bc, problems_t)+"'>"+bc+"%</span>\n"
        report += "<br/>\n"
    
    if ARGS.src:
        report += "Source compatibility: <span class='"+get_bc_class(bc_src, problems_t_src)+"'>"+bc_src+"%</span>\n"
        report += "<br/>\n"
    
    report += "</span>\n"
    
    report += "<h2>Analyzed Packages</h2>\n"
    report += "<table class='summary'>\n"
    report += "<tr>\n"
    report += "<th>Old</th><th>New</th><th title='*.so, *.debug and header files'>Files</th>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<td class='object'>"+os.path.basename(PKGS["old"]["rel"])+"</td>\n"
    report += "<td class='object'>"+os.path.basename(PKGS["new"]["rel"])+"</td>\n"
    report += "<td class='center'>"+str(len(FILES["old"]["object"]))+"</td>\n"
    report += "</tr>\n"
    report += "<tr>\n"
    report += "<td class='object'>"+os.path.basename(PKGS["old"]["debug"])+"</td>\n"
    report += "<td class='object'>"+os.path.basename(PKGS["new"]["debug"])+"</td>\n"
    report += "<td class='center'>"+str(len(FILES["old"]["debuginfo"]))+"</td>\n"
    report += "</tr>\n"
    
    if PUBLIC_ABI:
        report += "<tr>\n"
        report += "<td class='object'>"+os.path.basename(PKGS["old"]["devel"])+"</td>\n"
        report += "<td class='object'>"+os.path.basename(PKGS["new"]["devel"])+"</td>\n"
        report += "<td class='center'>"+str(len(FILES["old"]["header"]))+"</td>\n"
        report += "</tr>\n"
    
    report += "</table>\n"
    
    report += "<h2>Shared Objects</h2>\n"
    report += "<table class='summary'>\n"
    
    cols = 5
    if ARGS.bin and ARGS.src:
        report += "<tr>\n"
        report += "<th rowspan='2'>Object</th>\n"
        report += "<th colspan='2'>Compatibility</th>\n"
        report += "<th rowspan='2'>Added<br/>Symbols</th>\n"
        report += "<th rowspan='2'>Removed<br/>Symbols</th>\n"
        report += "<th rowspan='2'>Total<br/>Symbols</th>\n"
        report += "</tr>\n"
        
        report += "<tr>\n"
        report += "<th title='Binary compatibility'>BC</th>\n"
        report += "<th title='Source compatibility'>SC</th>\n"
        report += "</tr>\n"
    else:
        cols -= 1
        report += "<tr>\n"
        report += "<th>Object</th>\n"
        
        if ARGS.bin:
            report += "<th>Binary<br/>Compatibility</th>\n"
        else:
            report += "<th>Source<br/>Compatibility</th>\n"
        
        report += "<th>Added<br/>Symbols</th>\n"
        report += "<th>Removed<br/>Symbols</th>\n"
        report += "<th>Total<br/>Symbols</th>\n"
        report += "</tr>\n"
    
    for obj in new_objects:
        if obj in added:
            report += "<tr>\n"
            report += "<td class='object'>"+obj+"</td>\n"
            report += "<td colspan=\'"+str(cols)+"\' class='added'>Added to package</td>\n"
            report += "</tr>\n"
    
    for obj in old_objects:
        report += "<tr>\n"
        
        name = obj
        
        if obj in mapped:
            if obj in changed_soname:
                name += "<br/>"
                name += "<br/>"
                name += "<span class='incompatible'>(changed SONAME from<br/>\""+soname["old"][obj]+"\"<br/>to<br/>\""+changed_soname[obj]+"\")</span>"
            elif obj in renamed_object:
                name += "<br/>"
                name += "<br/>"
                name += "<span class='incompatible'>(changed file name from<br/>\""+obj+"\"<br/>to<br/>\""+renamed_object[obj]+"\")</span>"
        
        report += "<td class='object'>"+name+"</td>\n"
        
        if obj in mapped:
            if obj not in compat:
                for i in range(0, cols):
                    report += "<td>N/A</td>\n"
                continue
            
            if ARGS.bin:
                rate = 100 - float(compat[obj]["bin"]["affected"])
                added_symbols = compat[obj]["bin"]["added"]
                removed_symbols = compat[obj]["bin"]["removed"]
                total = compat[obj]["bin"]["total"]
                cclass = get_bc_class(rate, total)
                rpath = compat[obj]["bin"]["path"]
            
            if ARGS.src:
                rate_src = 100 - float(compat[obj]["src"]["affected"])
                added_symbols_src = compat[obj]["src"]["added"]
                removed_symbols_src = compat[obj]["src"]["removed"]
                total_src = compat[obj]["src"]["total"]
                cclass_src = get_bc_class(rate_src, total_src)
                rpath_src = compat[obj]["src"]["path"]
            
            if ARGS.bin:
                report += "<td class=\'"+cclass+"\'>"
                report += "<a href='"+rpath+"'>"+format_num(rate)+"%</a>"
                report += "</td>\n"
            
            if ARGS.src:
                report += "<td class=\'"+cclass_src+"\'>"
                report += "<a href='"+rpath_src+"'>"+format_num(rate_src)+"%</a>"
                report += "</td>\n"
            
            if not ARGS.bin:
                if int(added_symbols_src)>0:
                    report += "<td class='added'><a class='num' href='"+rpath_src+"#Added'>"+added_symbols_src+" new</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
                
                if int(removed_symbols_src)>0:
                    report += "<td class='removed'><a class='num' href='"+rpath_src+"#Removed'>"+removed_symbols_src+" removed</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
            else:
                if int(added_symbols)>0:
                    report += "<td class='added'><a class='num' href='"+rpath+"#Added'>"+added_symbols+" new</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
                
                if int(removed_symbols)>0:
                    report += "<td class='removed'><a class='num' href='"+rpath+"#Removed'>"+removed_symbols+" removed</a></td>\n"
                else:
                    report += "<td class='ok'>0</td>\n"
            
            report += "<td>"+str(object_symbols[obj])+"</td>\n"
        elif obj in removed:
            report += "<td colspan=\'"+str(cols)+"\' class='removed'>Removed from package</td>\n"
        
        report += "</tr>\n"
    
    report += "</table>\n"
    
    report += "<br/>\n"
    report += "<br/>\n"
    
    report += "<hr/>\n"
    report += "<div class='footer' align='right'><i>Generated by <a href='https://github.com/lvc/pkg-abidiff'>Pkg-ABIdiff</a> "+TOOL_VERSION+" &#160;</i></div>\n"
    
    report = compose_html_head(title, keywords, desc)+"<body>\n"+report+"\n</body>\n</html>\n"
    
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)
    
    write_file(report_dir+"/index.html", report)
    print "The report has been generated to: "+report_dir
    
    res = []
    
    if ARGS.bin:
        res.append("Avg. BC: "+bc+"%")
    
    if ARGS.src:
        res.append("Avg. SC: "+bc_src+"%")
    
    print ", ".join(res)
    
    s_exit(0)

scenario()
