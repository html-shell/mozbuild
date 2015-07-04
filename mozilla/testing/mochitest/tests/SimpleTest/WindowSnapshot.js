var gWindowUtils;

try {
  gWindowUtils = SpecialPowers.getDOMWindowUtils(window);
  if (gWindowUtils && !gWindowUtils.compareCanvases)
    gWindowUtils = null;
} catch (e) {
  gWindowUtils = null;
}

function snapshotWindow(win, withCaret) {
  return SpecialPowers.snapshotWindow(win, withCaret);
}

// If the two snapshots don't compare as expected (true for equal, false for
// unequal), returns their serializations as data URIs.  In all cases, returns
// whether the comparison was as expected.
function compareSnapshots(s1, s2, expectEqual, fuzz) {
  if (s1.width != s2.width || s1.height != s2.height) {
    ok(false, "Snapshot canvases are not the same size - comparing them makes no sense");
    return [false];
  }
  var passed = false;
  var numDifferentPixels;
  var maxDifference = { value: undefined };
  if (gWindowUtils) {
    var equal;
    try {
      numDifferentPixels = gWindowUtils.compareCanvases(s1, s2, maxDifference);
      if (!fuzz) {
        equal = (numDifferentPixels == 0);
      } else {
        equal = (numDifferentPixels <= fuzz.numDifferentPixels &&
                 maxDifference.value <= fuzz.maxDifference);
      }
      passed = (equal == expectEqual);
    } catch (e) {
      ok(false, "Exception thrown from compareCanvases: " + e);
    }
  }

  var s1DataURI, s2DataURI;
  if (!passed) {
    s1DataURI = s1.toDataURL();
    s2DataURI = s2.toDataURL();

    if (!gWindowUtils) {
      passed = ((s1DataURI == s2DataURI) == expectEqual);
    }
  }

  return [passed, s1DataURI, s2DataURI, numDifferentPixels, maxDifference.value];
}

function assertSnapshots(s1, s2, expectEqual, fuzz, s1name, s2name) {
  var [passed, s1DataURI, s2DataURI, numDifferentPixels, maxDifference] =
    compareSnapshots(s1, s2, expectEqual, fuzz);
  var sym = expectEqual ? "==" : "!=";
  ok(passed, "reftest comparison: " + sym + " " + s1name + " " + s2name);
  if (!passed) {
    // The language / format in this message should match the failure messages
    // displayed by reftest.js's "RecordResult()" method so that log output
    // can be parsed by reftest-analyzer.xhtml
    var report = "REFTEST TEST-UNEXPECTED-FAIL | " + s1name +
                 " | image comparison (" + sym + "), max difference: " +
                 maxDifference + ", number of differing pixels: " +
                 numDifferentPixels + "\n";
    if (expectEqual) {
      report += "REFTEST   IMAGE 1 (TEST): " + s1DataURI + "\n";
      report += "REFTEST   IMAGE 2 (REFERENCE): " + s2DataURI + "\n";
    } else {
      report += "REFTEST   IMAGE: " + s1DataURI + "\n";
    }
    dump(report);
  }
  return passed;
}
