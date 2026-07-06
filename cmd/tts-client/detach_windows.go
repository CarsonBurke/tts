//go:build windows

package main

import (
	"os/exec"
	"syscall"
)

const (
	windowsCreateNewProcessGroup = 0x00000200
	windowsDetachedProcess       = 0x00000008
)

func detachCommand(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: windowsCreateNewProcessGroup | windowsDetachedProcess,
	}
}
