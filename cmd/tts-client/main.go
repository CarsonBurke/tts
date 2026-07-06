package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

var daemonPackage = "tts[kokoro] @ git+https://github.com/CarsonBurke/tts@main"
var daemonPython = "3.12"

const (
	defaultIdleSeconds  = 30 * 60
	defaultStartTimeout = 45 * time.Second
)

type daemonState struct {
	Host  string `json:"host"`
	Port  int    `json:"port"`
	Token string `json:"token"`
	PID   int    `json:"pid"`
	Ready bool   `json:"ready"`
}

type daemonResponse struct {
	OK         bool    `json:"ok"`
	Ready      bool    `json:"ready"`
	Error      string  `json:"error"`
	Backend    string  `json:"backend"`
	SampleRate int     `json:"sample_rate"`
	OutputPath *string `json:"output_path"`
}

func main() {
	if len(os.Args) < 2 {
		execDaemon(os.Args[1:])
	}

	switch os.Args[1] {
	case "speak":
		runSpeak(os.Args[2:])
	case "daemon":
		runDaemon(os.Args[2:])
	default:
		execDaemon(os.Args[1:])
	}
}

func runSpeak(args []string) {
	if shouldBypassDaemon(args) {
		execDaemon(append([]string{"speak"}, args...))
	}

	state, err := liveState()
	if err != nil || !state.Ready {
		if err := startDaemonForSpeak(args); err != nil {
			fallbackOrDie(args, err)
		}
		state, err = liveState()
		if err != nil || !state.Ready {
			if err == nil {
				err = errors.New("daemon is not ready")
			}
			fallbackOrDie(args, err)
		}
	}

	response, err := request(state, map[string]any{
		"command":    "speak_args",
		"args":       args,
		"cwd":        mustGetwd(),
		"tts_config": os.Getenv("TTS_CONFIG"),
	}, 0)
	if err != nil || !response.OK {
		if err == nil {
			err = errors.New(response.Error)
		}
		fallbackOrDie(args, err)
	}

	if hasFlag(args, "--print-result") {
		if response.OutputPath != nil && *response.OutputPath != "" {
			fmt.Println(*response.OutputPath)
		} else if response.Backend != "" {
			fmt.Println(response.Backend)
		}
	}
}

func runDaemon(args []string) {
	if len(args) == 0 {
		execDaemon(append([]string{"daemon"}, args...))
	}
	switch args[0] {
	case "status":
		state, err := liveState()
		if err != nil {
			fmt.Println("not running")
			return
		}
		ready := "starting"
		if state.Ready {
			ready = "ready"
		}
		fmt.Printf("running %s pid=%d port=%d\n", ready, state.PID, state.Port)
	case "stop":
		state, err := readState()
		if err != nil {
			fmt.Println("not running")
			return
		}
		_, _ = request(state, map[string]any{"command": "stop"}, 2*time.Second)
		fmt.Println("stopped")
	case "start":
		options := daemonOptionsFromDaemonArgs(args[1:])
		state, err := liveState()
		if err == nil {
			printDaemonStatus(state)
			return
		}
		if err := startDaemon(options); err != nil {
			fmt.Fprintln(os.Stderr, "tts:", err)
			os.Exit(1)
		}
		state, err = waitForReady(options.startTimeout)
		if err != nil {
			fmt.Fprintln(os.Stderr, "tts:", err)
			os.Exit(1)
		}
		if !hasFlag(args[1:], "--quiet") {
			printDaemonStatus(state)
		}
	default:
		execDaemon(append([]string{"daemon"}, args...))
	}
}

func printDaemonStatus(state daemonState) {
	ready := "starting"
	if state.Ready {
		ready = "ready"
	}
	fmt.Printf("running %s pid=%d port=%d\n", ready, state.PID, state.Port)
}

type daemonOptions struct {
	configPath   string
	noConfig     bool
	idleSeconds  int
	startTimeout time.Duration
}

func startDaemonForSpeak(speakArgs []string) error {
	options := daemonOptionsFromSpeakArgs(speakArgs)
	if err := startDaemon(options); err != nil {
		return err
	}
	_, err := waitForReady(options.startTimeout)
	return err
}

func startDaemon(options daemonOptions) error {
	_ = os.Remove(statePath())
	if err := os.MkdirAll(runtimeDir(), 0o700); err != nil {
		return err
	}
	command, err := daemonCommand(daemonServeArgs(options))
	if err != nil {
		return err
	}
	log, err := os.OpenFile(logPath(), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	defer log.Close()
	cmd := exec.Command(command.path, command.args...)
	cmd.Stdin = nil
	cmd.Stdout = log
	cmd.Stderr = log
	detachCommand(cmd)
	if err := cmd.Start(); err != nil {
		return err
	}
	return nil
}

func daemonServeArgs(options daemonOptions) []string {
	args := []string{"daemon", "serve", "--idle-seconds", strconv.Itoa(options.idleSeconds)}
	if options.configPath != "" {
		args = append(args, "--config", options.configPath)
	}
	return args
}

func waitForReady(timeout time.Duration) (daemonState, error) {
	deadline := time.Now().Add(timeout)
	var lastErr error
	for time.Now().Before(deadline) {
		state, err := liveState()
		if err == nil && state.Ready {
			return state, nil
		}
		if err != nil {
			lastErr = err
		}
		time.Sleep(100 * time.Millisecond)
	}
	if lastErr != nil {
		return daemonState{}, fmt.Errorf("daemon did not become ready within %.0fs: %w", timeout.Seconds(), lastErr)
	}
	return daemonState{}, fmt.Errorf("daemon did not become ready within %.0fs", timeout.Seconds())
}

func fallbackOrDie(speakArgs []string, cause error) {
	if hasFlag(speakArgs, "--daemon-required") {
		fmt.Fprintln(os.Stderr, "tts:", cause)
		os.Exit(1)
	}
	execDaemon(append([]string{"speak"}, speakArgs...))
}

func shouldBypassDaemon(args []string) bool {
	if hasFlag(args, "--no-daemon") ||
		hasFlag(args, "--text-stdin") ||
		hasFlag(args, "--print-config-path") ||
		hasFlag(args, "--help") ||
		hasFlag(args, "-h") {
		return true
	}
	if backend := optionValue(args, "--backend"); backend != "" && backend != "kokoro" {
		return true
	}
	return false
}

func liveState() (daemonState, error) {
	state, err := readState()
	if err != nil {
		return daemonState{}, err
	}
	response, err := request(state, map[string]any{"command": "ping"}, 250*time.Millisecond)
	if err != nil || !response.OK {
		_ = os.Remove(statePath())
		if err != nil {
			return daemonState{}, err
		}
		return daemonState{}, errors.New(response.Error)
	}
	state.Ready = response.Ready
	return state, nil
}

func request(state daemonState, payload map[string]any, timeout time.Duration) (daemonResponse, error) {
	payload["token"] = state.Token
	address := net.JoinHostPort(state.Host, strconv.Itoa(state.Port))
	dialer := net.Dialer{}
	if timeout > 0 {
		dialer.Timeout = timeout
	}
	conn, err := dialer.Dial("tcp", address)
	if err != nil {
		return daemonResponse{}, err
	}
	defer conn.Close()
	if timeout > 0 {
		_ = conn.SetDeadline(time.Now().Add(timeout))
	}
	if err := json.NewEncoder(conn).Encode(payload); err != nil {
		return daemonResponse{}, err
	}
	line, err := bufio.NewReader(conn).ReadBytes('\n')
	if err != nil {
		return daemonResponse{}, err
	}
	var response daemonResponse
	if err := json.Unmarshal(line, &response); err != nil {
		return daemonResponse{}, err
	}
	return response, nil
}

func readState() (daemonState, error) {
	data, err := os.ReadFile(statePath())
	if err != nil {
		return daemonState{}, err
	}
	var state daemonState
	if err := json.Unmarshal(data, &state); err != nil {
		return daemonState{}, err
	}
	if state.Host == "" || state.Port == 0 || state.Token == "" {
		return daemonState{}, errors.New("invalid daemon state")
	}
	return state, nil
}

func statePath() string {
	return filepath.Join(runtimeDir(), "daemon.json")
}

func logPath() string {
	return filepath.Join(runtimeDir(), "daemon.log")
}

func runtimeDir() string {
	if value := os.Getenv("TTS_RUNTIME_DIR"); value != "" {
		return value
	}
	base := os.Getenv("XDG_RUNTIME_DIR")
	if base == "" {
		base = os.TempDir()
	}
	return filepath.Join(base, "tts-"+userID())
}

func userID() string {
	if runtime.GOOS == "windows" {
		if value := os.Getenv("USERNAME"); value != "" {
			return sanitize(value)
		}
		if value := os.Getenv("USER"); value != "" {
			return sanitize(value)
		}
	}
	current, err := user.Current()
	if err == nil {
		if current.Uid != "" {
			return sanitize(current.Uid)
		}
		if current.Username != "" {
			return sanitize(current.Username)
		}
	}
	return "user"
}

func sanitize(value string) string {
	replacer := strings.NewReplacer("/", "_", "\\", "_", ":", "_", " ", "_")
	return replacer.Replace(value)
}

func execDaemon(args []string) {
	command, err := daemonCommand(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, "tts:", err)
		os.Exit(1)
	}
	cmd := exec.Command(command.path, command.args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			os.Exit(exit.ProcessState.ExitCode())
		}
		fmt.Fprintln(os.Stderr, "tts:", err)
		os.Exit(1)
	}
}

type commandSpec struct {
	path string
	args []string
}

func daemonCommand(args []string) (commandSpec, error) {
	if override := os.Getenv("TTS_DAEMON_COMMAND"); override != "" {
		parts := strings.Fields(override)
		if len(parts) == 0 {
			return commandSpec{}, errors.New("TTS_DAEMON_COMMAND is empty")
		}
		return commandSpec{path: parts[0], args: append(parts[1:], args...)}, nil
	}
	uv, err := exec.LookPath("uv")
	if err != nil {
		return commandSpec{}, errors.New("uv is required to install/run the Python TTS daemon; install it from https://docs.astral.sh/uv/getting-started/installation/ or set TTS_DAEMON_COMMAND")
	}
	pkg := os.Getenv("TTS_DAEMON_PACKAGE")
	if pkg == "" {
		pkg = daemonPackage
	}
	python := os.Getenv("TTS_DAEMON_PYTHON")
	if python == "" {
		python = daemonPython
	}
	uvArgs := []string{"tool", "run", "--python", python, "--from", pkg, "tts"}
	uvArgs = append(uvArgs, args...)
	return commandSpec{path: uv, args: uvArgs}, nil
}

func hasFlag(args []string, name string) bool {
	for _, arg := range args {
		if arg == name {
			return true
		}
	}
	return false
}

func optionValue(args []string, name string) string {
	prefix := name + "="
	for index, arg := range args {
		if strings.HasPrefix(arg, prefix) {
			return strings.TrimSpace(strings.TrimPrefix(arg, prefix))
		}
		if arg == name && index+1 < len(args) {
			return strings.TrimSpace(args[index+1])
		}
	}
	return ""
}

func configArg(args []string) string {
	return optionValue(args, "--config")
}

func daemonOptionsFromSpeakArgs(args []string) daemonOptions {
	options := defaultDaemonOptions()
	options.configPath = configArg(args)
	options.noConfig = hasFlag(args, "--no-config")
	options.applyConfigDefaults()
	if value := optionValue(args, "--daemon-idle-seconds"); value != "" {
		if parsed, err := strconv.Atoi(value); err == nil && parsed > 0 {
			options.idleSeconds = parsed
		}
	}
	if value := optionValue(args, "--daemon-start-timeout"); value != "" {
		if parsed, err := strconv.ParseFloat(value, 64); err == nil && parsed > 0 {
			options.startTimeout = time.Duration(parsed * float64(time.Second))
		}
	}
	return options
}

func daemonOptionsFromDaemonArgs(args []string) daemonOptions {
	options := defaultDaemonOptions()
	options.configPath = configArg(args)
	options.applyConfigDefaults()
	if value := optionValue(args, "--idle-seconds"); value != "" {
		if parsed, err := strconv.Atoi(value); err == nil && parsed > 0 {
			options.idleSeconds = parsed
		}
	}
	if value := optionValue(args, "--start-timeout"); value != "" {
		if parsed, err := strconv.ParseFloat(value, 64); err == nil && parsed > 0 {
			options.startTimeout = time.Duration(parsed * float64(time.Second))
		}
	}
	return options
}

func defaultDaemonOptions() daemonOptions {
	return daemonOptions{
		idleSeconds:  defaultIdleSeconds,
		startTimeout: defaultStartTimeout,
	}
}

func (options *daemonOptions) applyConfigDefaults() {
	if options.noConfig {
		return
	}
	path := options.configPath
	if path == "" {
		path = defaultConfigPath()
	}
	values := readSpeakConfig(path)
	if value := values["daemon_idle_seconds"]; value != "" {
		if parsed, err := strconv.Atoi(value); err == nil && parsed > 0 {
			options.idleSeconds = parsed
		}
	}
	if value := values["daemon_start_timeout"]; value != "" {
		if parsed, err := strconv.ParseFloat(value, 64); err == nil && parsed > 0 {
			options.startTimeout = time.Duration(parsed * float64(time.Second))
		}
	}
}

func defaultConfigPath() string {
	if value := os.Getenv("TTS_CONFIG"); value != "" {
		return value
	}
	home, _ := os.UserHomeDir()
	if runtime.GOOS == "windows" {
		if value := os.Getenv("APPDATA"); value != "" {
			return filepath.Join(value, "tts", "config.ini")
		}
		return filepath.Join(home, "AppData", "Roaming", "tts", "config.ini")
	}
	if runtime.GOOS == "darwin" {
		return filepath.Join(home, "Library", "Application Support", "tts", "config.ini")
	}
	if value := os.Getenv("XDG_CONFIG_HOME"); value != "" {
		return filepath.Join(value, "tts", "config.ini")
	}
	return filepath.Join(home, ".config", "tts", "config.ini")
}

func readSpeakConfig(path string) map[string]string {
	values := map[string]string{}
	data, err := os.ReadFile(path)
	if err != nil {
		return values
	}
	inSpeak := false
	for _, raw := range strings.Split(string(data), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			inSpeak = strings.EqualFold(strings.TrimSpace(line[1:len(line)-1]), "speak")
			continue
		}
		if !inSpeak {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			key, value, ok = strings.Cut(line, ":")
		}
		if !ok {
			continue
		}
		name := strings.ReplaceAll(strings.TrimSpace(key), "-", "_")
		values[name] = strings.TrimSpace(value)
	}
	return values
}

func mustGetwd() string {
	cwd, err := os.Getwd()
	if err != nil {
		return ""
	}
	return cwd
}
