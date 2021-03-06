import os
import tempfile

import idaapi
import idc
import idautils

from miasm2.core.bin_stream_ida import bin_stream_ida
from miasm2.core.asmblock import expr_is_label, AsmLabel, is_int
from miasm2.expression.simplifications import expr_simp
from miasm2.analysis.data_flow import dead_simp
from miasm2.ir.ir import AssignBlock, IRBlock

from utils import guess_machine, expr2colorstr


# Override Miasm asmblock default label naming convention to shrink block size
# in IDA

def label_init(self, name="", offset=None):
    self.fixedblocs = False
    if is_int(name):
        name = "loc_%X" % (int(name) & 0xFFFFFFFFFFFFFFFF)
    self.name = name
    self.attrib = None
    if offset is None:
        self.offset = None
    else:
        self.offset = int(offset)
def label_str(self):
    if isinstance(self.offset, (int, long)):
        return "%s:0x%x" % (self.name, self.offset)
    else:
        return "%s:%s" % (self.name, str(self.offset))

AsmLabel.__init__ = label_init
AsmLabel.__str__ = label_str

def color_irblock(irblock, ir_arch):
    out = []
    lbl = idaapi.COLSTR(str(irblock.label), idaapi.SCOLOR_INSN)
    out.append(lbl)
    for assignblk in irblock.irs:
        for dst, src in sorted(assignblk.iteritems()):
            dst_f = expr2colorstr(ir_arch.arch.regs.all_regs_ids, dst)
            src_f = expr2colorstr(ir_arch.arch.regs.all_regs_ids, src)
            line = idaapi.COLSTR("%s = %s" % (dst_f, src_f), idaapi.SCOLOR_INSN)
            out.append('    %s' % line)
        out.append("")
    out.pop()
    dst = str('    Dst: %s' % irblock.dst)
    dst = idaapi.COLSTR(dst, idaapi.SCOLOR_RPTCMT)
    out.append(dst)
    return "\n".join(out)


class GraphMiasmIR(idaapi.GraphViewer):

    def __init__(self, ir_arch, title, result):
        idaapi.GraphViewer.__init__(self, title)
        self.ir_arch = ir_arch
        self.result = result
        self.names = {}

    def OnRefresh(self):
        self.Clear()
        addr_id = {}
        for irblock in self.ir_arch.blocks.values():
            id_irblock = self.AddNode(color_irblock(irblock, self.ir_arch))
            addr_id[irblock] = id_irblock

        for irblock in self.ir_arch.blocks.values():
            if not irblock:
                continue
            all_dst = self.ir_arch.dst_trackback(irblock)
            for dst in all_dst:
                if not expr_is_label(dst):
                    continue

                dst = dst.name
                if not dst in self.ir_arch.blocks:
                    continue
                dst_block = self.ir_arch.blocks[dst]
                node1 = addr_id[irblock]
                node2 = addr_id[dst_block]
                self.AddEdge(node1, node2)
        return True

    def OnGetText(self, node_id):
        return str(self[node_id])

    def OnSelect(self, node_id):
        return True

    def OnClick(self, node_id):
        return True

    def OnCommand(self, cmd_id):
        if self.cmd_test == cmd_id:
            print 'TEST!'
            return
        print "command:", cmd_id

    def Show(self):
        if not idaapi.GraphViewer.Show(self):
            return False
        self.cmd_test = self.AddCommand("Test", "F2")
        if self.cmd_test == 0:
            print "Failed to add popup menu item!"
        return True


def build_graph(verbose=False, simplify=False):
    machine = guess_machine()
    mn, dis_engine, ira = machine.mn, machine.dis_engine, machine.ira

    if verbose:
        print "Arch", dis_engine

    fname = idc.GetInputFile()
    if verbose:
        print fname

    bs = bin_stream_ida()
    mdis = dis_engine(bs)
    ir_arch = ira(mdis.symbol_pool)

    # populate symbols with ida names
    for addr, name in idautils.Names():
        # print hex(ad), repr(name)
        if name is None:
            continue
        mdis.symbol_pool.add_label(name, addr)

    if verbose:
        print "start disasm"
    addr = idc.ScreenEA()
    if verbose:
        print hex(addr)

    blocks = mdis.dis_multiblock(addr)

    if verbose:
        print "generating graph"
        open('asm_flow.dot', 'w').write(blocks.dot())

        print "generating IR... %x" % addr

    for block in blocks:
        if verbose:
            print 'ADD'
            print block
        ir_arch.add_block(block)

    if verbose:
        print "IR ok... %x" % addr

    for irb in ir_arch.blocks.itervalues():
        irs = []
        for assignblk in irb.irs:
            new_assignblk = {
                expr_simp(dst): expr_simp(src)
                for dst, src in assignblk.iteritems()
            }
            irs.append(AssignBlock(new_assignblk, instr=assignblk.instr))
        ir_arch.blocks[irb.label] = IRBlock(irb.label, irs)

    if verbose:
        out = ir_arch.graph.dot()
        open(os.path.join(tempfile.gettempdir(), 'graph.dot'), 'wb').write(out)
    title = "Miasm IR graph"

    if simplify:
        dead_simp(ir_arch)

        ir_arch.simplify(expr_simp)
        modified = True
        while modified:
            modified = False
            modified |= dead_simp(ir_arch)
            modified |= ir_arch.remove_empty_assignblks()
            modified |= ir_arch.remove_jmp_blocks()
            modified |= ir_arch.merge_blocks()
        title += " (simplified)"

    g = GraphMiasmIR(ir_arch, title, None)

    g.cmd_a = g.AddCommand("cmd a", "x")
    g.cmd_b = g.AddCommand("cmd b", "y")

    g.Show()

if __name__ == "__main__":
    build_graph(verbose=True, simplify=False)
