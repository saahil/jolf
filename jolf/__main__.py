from read_klee_testcases import main as rkt_main
from read_klee_testcases import process_klee_out
#from read_afl_testcases import main as rat_main
import argparse
import os, sys, time, glob
from config import AFL_FUZZ, KLEE
import subprocess

PREFIXES = ["/home/ognawala/coreutils-8.30/", "/home/ognawala/coreutils-8.30-gcov/"]

def call_afl(max_time, seed_inputs_dir, output_dir, afl_object, argv):
    timeout = ["timeout", "--preserve-status", str(max_time)+"s"]
    afl_command = [AFL_FUZZ, "-i", seed_inputs_dir, "-o", output_dir, afl_object, argv, "@@"]
    try:
        ret = subprocess.check_call(timeout + afl_command)
    except subprocess.CalledProcessError:
        print("Exiting Jolf...")
        sys.exit(-1)

def get_max_size_in_queue(afl_seed_out_dirs):
    max_size = 0
    for d in afl_seed_out_dirs:
        for f in glob.glob(os.path.join(d, "queue") + "/id*"):
            if os.path.getsize(f)>max_size:
                max_size = os.path.getsize(f)
    return max_size

def call_klee(output_dir, max_time, klee_object, afl_seed_out_dirs):
    klee_command = [KLEE]
    
    # Seeding related arguments
    seeding_from_afl = []
    for d in afl_seed_out_dirs:
        seeding_from_afl += ["-afl-seed-out-dir="+d]
    if len(afl_seed_out_dirs)>0:
        seeding_from_afl += ["-named-seed-matching", "-allow-seed-extension", "-zero-seed-extension"]

    sym_file_size = get_max_size_in_queue(afl_seed_out_dirs)

    print(sym_file_size)
    libc = ["-posix-runtime", "-libc=uclibc"]
    output = ["-output-dir="+output_dir]
    timeout = ["-max-time="+str(max_time), "-watchdog"]
    other_args = ["-only-output-states-covering-new", "-max-instruction-time=10", "-optimize", "-suppress-external-warnings", "-write-cov"]
    sym_args = ["A", "--sym-args", "1", "2", "3", "--sym-files", "1", str(sym_file_size)]

    try:
        ret = subprocess.check_call(klee_command + seeding_from_afl + libc + output + timeout + other_args + [klee_object] + sym_args)
    except subprocess.CalledProcessError:
        print("Something wrong with the KLEE run...")
        #sys.exit(-1)

def clean_argv(argv):
    clean = []
# 
    for a in argv:
        stripped = a.strip("\x00\x01 ")
        
        if (stripped=="") or (stripped in clean):
            continue

        clean.append(stripped)

    return clean

def get_afl_command_args(d):
    fuzzer_stats = open(os.path.join(d, "fuzzer_stats"), "r")
    
    command_args = ""

    command_line = ""
    for l in fuzzer_stats:
        if l.startswith("command_line"):
            command_line = l

    toks = command_line.strip().split()
    
    i = len(toks)-1

    if not toks[i]=="@@":
        return command_args

    i -= 1

    while i>=0 and toks[i].startswith("-"):
       command_args += " " + toks[i]
       i -= 1

    return command_args

def get_afl_coverage(coverage_list, afl_out_dir):
    coverage_file = open(os.path.join(afl_out_dir, "cov/id-delta-cov"), "r")
    
    for line in coverage_file:
        if line.startswith("#"):
            continue
        fields = line.strip().split(", ")
        if not fields[2].startswith(PREFIXES[1]):
            continue
        if not fields[3]=="line":
            continue
        file_name = fields[2].split(PREFIXES[1])[-1]
        line_no = int(fields[4])

        if (file_name, line_no) not in coverage_list:
            coverage_list.append((file_name, line_no))

    return coverage_list

def get_klee_coverage(coverage_list, klee_out_dir):
    for f in glob.glob(klee_out_dir+"/*.cov"):
        cov_file = open(f, "r")
        for line in cov_file:
            if not line.startswith(PREFIXES[0]):
                continue
            file_name = line.strip().split(":")[0].split(PREFIXES[0])[-1]
            line_no = int(line.strip().split(":")[-1])
            if (file_name, line_no) not in coverage_list:
                coverage_list.append((file_name, line_no))

    return coverage_list

def sort_inputs_by_size(input_dirs):
    size_dict = {}
    for dir in input_dirs:
        for f in glob.glob(dir+"/id:*"):
            size = os.path.getsize(f)
            if size not in size_dict.keys():
                size_dict[size] = []
            size_dict[size].append(f)

    return size_dict

def main():
    parser = argparse.ArgumentParser(description="AFL+KLEE flipper")
    parser.add_argument("-t", "--max-time-each", help="Max time(sec) allowed for each round of KLEE or AFL")
    parser.add_argument("-i", "--seed-inputs-dir", help="Seed inputs for AFL")
    parser.add_argument("-o", "--all-output-dir", help="Folder to run experiments in")
    parser.add_argument("-k", "--klee-object", help="Bitcode for KLEE")
    parser.add_argument("-b", "--afl-object", help="Binary or LLVM IR for AFL")
    parser.add_argument("-c", "--coverage-mode", help="Run Jolf in coverage mode (results existing)")
    parser.add_argument("-g", "--coverage-source", help="Location of project compiled with gcov")
    parser.add_argument("-e", "--coverage-executable", help="Name of executable compiled with Gcov")
    
    args = parser.parse_args()
    seed_inputs_dir = os.path.join(args.all_output_dir, "all_seeds/")

    if args.coverage_mode=="True":
        print("Calculating coverage...")
        fuzzing_dirs = glob.glob(args.all_output_dir+"/fuzzing-*") + [args.all_output_dir+"/init-fuzzing/"]
        
        for d in fuzzing_dirs:
            if os.path.isdir(d+"/cov"):
                continue
            afl_command_args = get_afl_command_args(d)
            os.system("afl-cov -d %s --coverage-cmd \"%s %s AFL_FILE\" --code-dir %s --coverage-include-lines"%(d, args.coverage_executable, afl_command_args, args.coverage_source))

        coverage_list = []
        
        coverage_list = get_klee_coverage(coverage_list, args.all_output_dir+"/klee0")
        
        for d in fuzzing_dirs:
            coverage_list = get_afl_coverage(coverage_list, d)

        coverage_list = get_klee_coverage(coverage_list, args.all_output_dir+"/klee1")

        print("Covered lines: %d"%(len(coverage_list)))
        sys.exit(0)
    
    # Prepare directory
    if not os.path.isdir(args.all_output_dir):
        ret = subprocess.check_call(["mkdir", args.all_output_dir])
        ret = subprocess.check_call(["mkdir", seed_inputs_dir])
        os.system("cp " + os.path.join(args.seed_inputs_dir, "* ") + seed_inputs_dir)

    # First fuzz
    if not os.path.isdir(os.path.join(args.all_output_dir, "init-fuzzing")):
        call_afl(args.max_time_each, seed_inputs_dir, os.path.join(args.all_output_dir, "init-fuzzing"), args.afl_object, "")

    # Sort fuzzing test-cases by size
    file_size_dict = sort_inputs_by_size([os.path.join(os.path.join(args.all_output_dir, "init-fuzzing"), "queue")])

    # Concolic execution with AFL seeds - grouped by seed-input size
    if (int(args.max_time_each)/len(file_size_dict.keys()))<30:
        max_time_klee_instance = 30 
    else:
        max_time_klee_instance = int(args.max_time_each)/len(file_size_dict.keys())
    
    print("AFL inputs grouped into %d groups"%(len(file_size_dict.keys())))
    print("Allocating %f seconds for each KLEE instance"%(max_time_klee_instance))
    time.sleep(2)
    
    for i, s in enumerate(file_size_dict.keys()):
        if not os.path.isdir(os.path.join(args.all_output_dir, "klee"+str(i))):
            if os.path.isdir("/tmp/afl-seed-group"):
                os.system("rm -rf /tmp/afl-seed-group")
            os.system("mkdir /tmp/afl-seed-group")
            os.system("mkdir /tmp/afl-seed-group/queue")
            for f in file_size_dict[s]:
                os.system("cp %s /tmp/afl-seed-group/queue/"%(f))
            
            call_klee(os.path.join(args.all_output_dir, "klee"+str(i)), max_time_klee_instance, args.klee_object, ["/tmp/afl-seed-group"])

    # Read KLEE testcases and populate new seed-inputs folder
    argv = []
    for k in glob.glob(args.all_output_dir+"/klee*"):
        argv.extend(process_klee_out(k, seed_inputs_dir))
    
    argv = clean_argv(argv)
    print(argv)
    time.sleep(3)
    
    #sys.exit(-1)

    # How many sets of command line arguments were found by KLEE?
    if len(argv)>0:
        if (int(args.max_time_each)/len(argv))<30:
            max_time_fuzzing_instance = 30 
        else:
            max_time_fuzzing_instance = int(args.max_time_each)/len(argv)
    else:
        argv = [" "]
        max_time_fuzzing_instance = args.max_time_each
    
    # Second fuzzing round
    second_round_fuzzed_list = glob.glob(args.all_output_dir+"/fuzzing-*")
    if len(second_round_fuzzed_list)==0:
        for i, arg in enumerate(argv):
            call_afl(max_time_fuzzing_instance, seed_inputs_dir, os.path.join(args.all_output_dir, "fuzzing-"+str(i)), args.afl_object, arg)
    
    # Concolic execution again
    afl_seed_out_dirs = glob.glob(args.all_output_dir+"/fuzzing-*")
    if not os.path.isdir(os.path.join(args.all_output_dir, "klee1")):
        call_klee(os.path.join(args.all_output_dir, "klee1"), args.max_time_each, args.klee_object, afl_seed_out_dirs)

if __name__=="__main__":
    main()

