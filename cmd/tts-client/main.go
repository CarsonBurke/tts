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
		if err := startDaemon(args); err != nil {
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
	default:
		execDaemon(append([]string{"daemon"}, args...))
	}
}

func startDaemon(speakArgs []string) error {
	daemonPath, err := daemonExecutable()
	if err != nil {
		return err
	}
	args := []string{"daemon", "start", "--quiet"}
	if config := configArg(speakArgs); config != "" {
		args = append(args, "--config", config)
	}
	cmd := exec.Command(daemonPath, args...)
	cmd.Stdin = nil
	cmd.Stdout = nil
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func fallbackOrDie(speakArgs []string, cause error) {
	if hasFlag(speakArgs, "--daemon-required") {
		fmt.Fprintln(os.Stderr, "tts:", cause)
		os.Exit(1)
	}
	execDaemon(append([]string{"speak"}, speakArgs...))
}

func shouldBypassDaemon(args []string) bool {
	if hasFlag(args, "--no-daemon") || hasFlag(args, "--text-stdin") {
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

func daemonExecutable() (string, error) {
	self, err := os.Executable()
	if err != nil {
		return "", err
	}
	dir := filepath.Dir(self)
	name := executableName("tts-daemon")
	candidates := []string{
		filepath.Join(dir, name),
		filepath.Join(dir, "tts-daemon", name),
	}
	for _, candidate := range candidates {
		if isExecutable(candidate) {
			return candidate, nil
		}
	}
	if path, err := exec.LookPath(name); err == nil {
		return path, nil
	}
	return "", fmt.Errorf("could not find %s next to %s", name, self)
}

func execDaemon(args []string) {
	path, err := daemonExecutable()
	if err != nil {
		fmt.Fprintln(os.Stderr, "tts:", err)
		os.Exit(1)
	}
	cmd := exec.Command(path, args...)
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

func executableName(name string) string {
	if runtime.GOOS == "windows" {
		return name + ".exe"
	}
	return name
}

func isExecutable(path string) bool {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return false
	}
	if runtime.GOOS == "windows" {
		return true
	}
	return info.Mode()&0111 != 0
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

func mustGetwd() string {
	cwd, err := os.Getwd()
	if err != nil {
		return ""
	}
	return cwd
}
