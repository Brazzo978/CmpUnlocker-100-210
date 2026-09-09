// SPDX-License-Identifier: GPL-2.0-only
/*
 * Narrow CMP100/GV100 Nouveau ACR descriptor hook.
 *
 * The module accepts only PCI ID 10de:1d84 and changes only two exact stock
 * descriptor sizes. It does not contain a firmware blob or write the Tensor
 * register itself; the locally derived signed ACR payload performs that write.
 */

#include <linux/atomic.h>
#include <linux/io.h>
#include <linux/kprobes.h>
#include <linux/module.h>
#include <linux/pci.h>

#define TARGET_VENDOR           0x10de
#define TARGET_DEVICE           0x1d84
#define PMU_BASE                0x0010a000
#define SEC2_BASE               0x00840000
#define FLCN_MAILBOX0           0x040
#define FLCN_MAILBOX1           0x044
#define FLCN_DMEMC              0x1c0
#define FLCN_DMEMD              0x1c4
#define DMEM_DATA_SIZE_OFFSET   0x48
#define DMEMC_AUTOINCREMENT     BIT(24)
#define DMEMC_READ              BIT(25)
#define STOCK_DATA_SIZE         0x600
#define TEST_DATA_SIZE          0x700
#define LOAD_STOCK_DATA_SIZE    0x1000

static bool armed;
module_param(armed, bool, 0400);
MODULE_PARM_DESC(armed, "Enable exact-match descriptor replacement");

static uint bus = 2;
module_param(bus, uint, 0400);
MODULE_PARM_DESC(bus, "PCI bus of the isolated CMP100 (domain 0, device 00.0)");

static uint replace_on_return = 2;
module_param(replace_on_return, uint, 0400);
MODULE_PARM_DESC(replace_on_return,
	"Replace only this gp108_acr_hsfw_load_bld return");

static uint load_test_size = 0x5d00;
module_param(load_test_size, uint, 0400);
MODULE_PARM_DESC(load_test_size,
	"SEC2 load descriptor size; accepted values: 0x1100, 0x5c00, 0x5d00");

static struct pci_dev *gpu;
static void __iomem *bar0;
static atomic64_t returns_seen = ATOMIC64_INIT(0);
static atomic64_t replacements = ATOMIC64_INIT(0);
static atomic64_t boot_returns_seen = ATOMIC64_INIT(0);
static atomic64_t descriptors_seen = ATOMIC64_INIT(0);

/* Observe the host descriptor at the exact nvkm_falcon_pio_wr() entry. */
static int pio_wr_pre(struct kprobe *probe, struct pt_regs *regs)
{
	u32 *src = (u32 *)regs_get_kernel_argument(regs, 1);
	unsigned long src_base = regs_get_kernel_argument(regs, 2);
	unsigned long src_size = regs_get_kernel_argument(regs, 3);
	unsigned long mem_type = regs_get_kernel_argument(regs, 4);
	unsigned long mem_base = regs_get_kernel_argument(regs, 5);
	u32 d[21];
	u32 before;
	u32 after;
	u64 seen;

	(void)probe;
	if (!src || src_base != 0 || src_size != 0 || mem_type != 1 ||
	    mem_base != 0)
		return 0;
	memcpy(d, src, sizeof(d));

	/* flcn_bl_dmem_desc_v2: ctx=1, NS size=0x100, entry=0. */
	if (d[8] != 1 || d[12] != 0x100 || d[15] != 0 ||
	    (d[18] != STOCK_DATA_SIZE && d[18] != LOAD_STOCK_DATA_SIZE))
		return 0;

	seen = atomic64_inc_return(&descriptors_seen);
	before = d[18];
	after = before;
	if (READ_ONCE(armed) && before == LOAD_STOCK_DATA_SIZE) {
		WRITE_ONCE(src[18], READ_ONCE(load_test_size));
		after = READ_ONCE(src[18]);
		if (after == READ_ONCE(load_test_size))
			atomic64_inc(&replacements);
	}
	pr_notice("gv100_nouveau_acr_hook: PIO_DESC seen=%llu ctx=%#x ns_off=%#x ns_size=%#x hs_off=%#x hs_size=%#x entry=%#x data_size_before=%#x after=%#x replaced=%u\n",
		  (unsigned long long)seen, d[8], d[11], d[12], d[13], d[14],
		  d[15], before, after,
		  before == LOAD_STOCK_DATA_SIZE &&
		  after == READ_ONCE(load_test_size));
	return 0;
}

static struct kprobe pio_wr_probe = {
	.symbol_name = "nvkm_falcon_pio_wr",
	.pre_handler = pio_wr_pre,
};

static u32 flcn_dmem_read(u32 base, u32 address)
{
	iowrite32(DMEMC_READ | (address & 0xffff), bar0 + base + FLCN_DMEMC);
	return ioread32(bar0 + base + FLCN_DMEMD);
}

static void flcn_dmem_write(u32 base, u32 address, u32 value)
{
	iowrite32((address & 0xffff) | DMEMC_AUTOINCREMENT,
		  bar0 + base + FLCN_DMEMC);
	iowrite32(value, bar0 + base + FLCN_DMEMD);
}

static int load_bld_return(struct kretprobe_instance *instance,
			   struct pt_regs *regs)
{
	u64 call = atomic64_inc_return(&returns_seen);
	u32 before;
	u32 after;

	(void)instance;
	(void)regs;
	before = flcn_dmem_read(PMU_BASE, DMEM_DATA_SIZE_OFFSET);
	after = before;

	if (READ_ONCE(armed) && call == READ_ONCE(replace_on_return) &&
	    before == STOCK_DATA_SIZE) {
		flcn_dmem_write(PMU_BASE, DMEM_DATA_SIZE_OFFSET, TEST_DATA_SIZE);
		after = flcn_dmem_read(PMU_BASE, DMEM_DATA_SIZE_OFFSET);
		if (after == TEST_DATA_SIZE)
			atomic64_inc(&replacements);
	}

	pr_notice("gv100_nouveau_acr_hook: return=%llu armed=%u pmu_d48_before=%#x after=%#x replaced=%u\n",
		  (unsigned long long)call, READ_ONCE(armed), before, after,
		  before == STOCK_DATA_SIZE && after == TEST_DATA_SIZE);
	return 0;
}

static struct kretprobe load_bld_probe = {
	.kp.symbol_name = "gp108_acr_hsfw_load_bld",
	.handler = load_bld_return,
	.maxactive = 16,
};

static int falcon_boot_return(struct kretprobe_instance *instance,
			      struct pt_regs *regs)
{
	u64 call = atomic64_inc_return(&boot_returns_seen);
	u32 d600 = flcn_dmem_read(PMU_BASE, 0x600);
	u32 d6fc = flcn_dmem_read(PMU_BASE, 0x6fc);
	u32 mailbox0 = ioread32(bar0 + PMU_BASE + FLCN_MAILBOX0);
	u32 mailbox1 = ioread32(bar0 + PMU_BASE + FLCN_MAILBOX1);
	u32 sec2_mailbox0 = ioread32(bar0 + SEC2_BASE + FLCN_MAILBOX0);
	u32 sec2_mailbox1 = ioread32(bar0 + SEC2_BASE + FLCN_MAILBOX1);
	u32 fecs_plm = ioread32(bar0 + 0x00409650);
	u32 tensor = ioread32(bar0 + 0x00409664);

	(void)instance;
	pr_notice("gv100_nouveau_acr_hook: BOOT_RETURN call=%llu function_rc=%ld pmu_d600=%#010x pmu_d6fc=%#010x pmu_mbox0=%#010x pmu_mbox1=%#010x sec2_mbox0=%#010x sec2_mbox1=%#010x plm_409650=%#010x tensor_409664=%#010x\n",
		  (unsigned long long)call, regs_return_value(regs), d600, d6fc,
		  mailbox0, mailbox1, sec2_mailbox0, sec2_mailbox1, fecs_plm,
		  tensor);
	return 0;
}

static struct kretprobe falcon_boot_probe = {
	.kp.symbol_name = "gm200_flcn_fw_boot",
	.handler = falcon_boot_return,
	.maxactive = 16,
};

static int __init gv100_nouveau_acr_hook_init(void)
{
	int ret;

	if (bus > 0xff)
		return -EINVAL;
	if (load_test_size != 0x1100 && load_test_size != 0x5c00 &&
	    load_test_size != 0x5d00)
		return -EINVAL;

	gpu = pci_get_domain_bus_and_slot(0, bus, PCI_DEVFN(0, 0));
	if (!gpu)
		return -ENODEV;
	if (gpu->vendor != TARGET_VENDOR || gpu->device != TARGET_DEVICE) {
		ret = -ENODEV;
		goto fail_put;
	}

	bar0 = pci_iomap(gpu, 0, 0);
	if (!bar0) {
		ret = -ENOMEM;
		goto fail_put;
	}

	ret = register_kprobe(&pio_wr_probe);
	if (ret)
		goto fail_unmap;
	ret = register_kretprobe(&load_bld_probe);
	if (ret) {
		unregister_kprobe(&pio_wr_probe);
		goto fail_unmap;
	}
	ret = register_kretprobe(&falcon_boot_probe);
	if (ret) {
		unregister_kretprobe(&load_bld_probe);
		unregister_kprobe(&pio_wr_probe);
		goto fail_unmap;
	}

	pr_info("gv100_nouveau_acr_hook: observing %s on 0000:%02x:00.0 armed=%u replace_on_return=%u\n",
		load_bld_probe.kp.symbol_name, bus, armed, replace_on_return);
	return 0;

fail_unmap:
	pci_iounmap(gpu, bar0);
	bar0 = NULL;
fail_put:
	pci_dev_put(gpu);
	gpu = NULL;
	return ret;
}

static void __exit gv100_nouveau_acr_hook_exit(void)
{
	unregister_kretprobe(&falcon_boot_probe);
	unregister_kretprobe(&load_bld_probe);
	unregister_kprobe(&pio_wr_probe);
	if (bar0)
		pci_iounmap(gpu, bar0);
	if (gpu)
		pci_dev_put(gpu);
	pr_info("gv100_nouveau_acr_hook: removed returns=%lld replacements=%lld boot_returns=%lld descriptors=%lld missed=%d/%d\n",
		(long long)atomic64_read(&returns_seen),
		(long long)atomic64_read(&replacements),
		(long long)atomic64_read(&boot_returns_seen),
		(long long)atomic64_read(&descriptors_seen),
		load_bld_probe.nmissed, falcon_boot_probe.nmissed);
}

module_init(gv100_nouveau_acr_hook_init);
module_exit(gv100_nouveau_acr_hook_exit);

MODULE_AUTHOR("CMP100 investigation");
MODULE_DESCRIPTION("Narrow GV100 Nouveau ACR descriptor hook");
MODULE_LICENSE("GPL");
